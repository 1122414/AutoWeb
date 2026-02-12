# ==============================================================================
# Code Cache Manager - 代码缓存复用系统
# ==============================================================================
# 核心功能：
# 1. 将成功执行的代码存入 Milvus 向量库
# 2. 根据任务描述 + DOM 结构检索相似代码
# 3. 复用历史代码，减少 Token 消耗
# ==============================================================================
import atexit
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, NamedTuple, Optional, Tuple
from urllib.parse import urlparse

from pymilvus import (
    AnnSearchRequest,
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    WeightedRanker,
    utility,
)

from config import (
    CODE_CACHE_COLLECTION,
    CODE_CACHE_WEIGHT_GOAL,
    CODE_CACHE_WEIGHT_LOCATOR,
    CODE_CACHE_WEIGHT_URL,
    CODE_CACHE_WEIGHT_USER_TASK,
    MILVUS_URI,
)
from skills.vector_gateway import (
    connect_milvus,
    hybrid_search,
    insert_and_flush,
    normalize_weights,
    read_hit_field,
)


class CacheHit(NamedTuple):
    id: str
    code: str
    score: float
    url_pattern: str
    goal: str
    success_count: int
    user_task: str = ""


def extract_param_diffs(cached_task: str, current_task: str) -> list:
    import difflib
    import re as _re

    def _tokenize(text: str) -> list:
        return _re.findall(r"[a-zA-Z0-9_]+|\S", text)

    old_tokens = _tokenize(cached_task)
    new_tokens = _tokenize(current_task)
    matcher = difflib.SequenceMatcher(None, old_tokens, new_tokens)

    diffs = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace":
            continue
        old_val = "".join(old_tokens[i1:i2])
        new_val = "".join(new_tokens[j1:j2])
        if len(old_val) >= 2 and len(new_val) >= 2:
            diffs.append((old_val, new_val))

    diffs.sort(key=lambda x: len(x[0]), reverse=True)
    return diffs


def apply_param_substitution(code: str, diffs: list) -> str:
    import re as _re

    for old_val, new_val in diffs:
        pattern = _re.compile(
            r"""(['"])([^'"]*?)""" + _re.escape(old_val) + r"""([^'"]*?)\1"""
        )
        code = pattern.sub(
            lambda m: f"{m.group(1)}{m.group(2)}{new_val}{m.group(3)}{m.group(1)}",
            code,
        )
    return code


class CodeCacheManager:
    SIMILARITY_THRESHOLD = 0.0
    DUPLICATE_THRESHOLD = 0.90
    NAVIGATION_CODE_MAX_LENGTH = 200
    MAX_CODE_WARN = 6400

    def __init__(self):
        self._collection: Optional[Collection] = None
        self._embeddings = None
        self._vector_dim: Optional[int] = None
        self._weights = normalize_weights(
            (
                CODE_CACHE_WEIGHT_GOAL,
                CODE_CACHE_WEIGHT_LOCATOR,
                CODE_CACHE_WEIGHT_USER_TASK,
                CODE_CACHE_WEIGHT_URL,
            ),
            defaults=(0.6, 0.2, 0.1, 0.1),
            tag="CodeCache",
        )
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="CodeCache")
        atexit.register(self._shutdown)

    def _get_embeddings(self):
        if self._embeddings is None:
            from rag.retriever_qa import get_embedding_model

            self._embeddings = get_embedding_model()
        return self._embeddings

    def _get_vector_dim(self) -> int:
        if self._vector_dim is None:
            vec = self._get_embeddings().embed_query("code_cache_dimension_probe")
            self._vector_dim = len(vec)
        return self._vector_dim

    def _schema_fields(self, dim: int) -> List[FieldSchema]:
        return [
            FieldSchema("pk", DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema("goal_vector", DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema("locator_vector", DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema("user_task_vector", DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema("url_vector", DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema("goal", DataType.VARCHAR, max_length=2000),
            FieldSchema("locator_info", DataType.VARCHAR, max_length=6400),
            FieldSchema("user_task", DataType.VARCHAR, max_length=6400),
            FieldSchema("url_pattern", DataType.VARCHAR, max_length=512),
            FieldSchema("code", DataType.VARCHAR, max_length=16000),
            FieldSchema("cache_id", DataType.VARCHAR, max_length=128),
            FieldSchema("dom_hash", DataType.VARCHAR, max_length=64),
            FieldSchema("success_count", DataType.INT64),
            FieldSchema("fail_count", DataType.INT64),
            FieldSchema("created_at", DataType.VARCHAR, max_length=64),
            FieldSchema("updated_at", DataType.VARCHAR, max_length=64),
        ]

    def _is_schema_compatible(self, collection: Collection, dim: int) -> bool:
        required = {
            "goal_vector",
            "locator_vector",
            "user_task_vector",
            "url_vector",
            "goal",
            "locator_info",
            "user_task",
            "url_pattern",
            "code",
            "cache_id",
            "dom_hash",
            "success_count",
            "fail_count",
            "created_at",
            "updated_at",
        }
        fields = {f.name: f for f in collection.schema.fields}
        if not required.issubset(fields.keys()):
            return False

        for name in ("goal_vector", "locator_vector", "user_task_vector", "url_vector"):
            field = fields[name]
            if field.dtype != DataType.FLOAT_VECTOR:
                return False
            if int(field.params.get("dim", -1)) != dim:
                return False
        return True

    def _create_collection(self, dim: int) -> Collection:
        schema = CollectionSchema(
            fields=self._schema_fields(dim),
            description="AutoWeb code cache with multi-vector hybrid retrieval",
            enable_dynamic_field=True,
        )
        collection = Collection(
            name=CODE_CACHE_COLLECTION,
            schema=schema,
            consistency_level="Bounded",
        )

        vector_index = {"metric_type": "COSINE",
                        "index_type": "AUTOINDEX", "params": {}}
        collection.create_index(field_name="goal_vector",
                                index_params=vector_index)
        collection.create_index(
            field_name="locator_vector", index_params=vector_index)
        collection.create_index(
            field_name="user_task_vector", index_params=vector_index)
        collection.create_index(field_name="url_vector",
                                index_params=vector_index)
        collection.create_index(field_name="url_pattern", index_params={
                                "index_type": "INVERTED"})
        collection.load()
        print(
            f"✅ [CodeCache] Created collection '{CODE_CACHE_COLLECTION}' (dim={dim})")
        return collection

    def _ensure_collection(self) -> Collection:
        if self._collection is not None:
            return self._collection

        connect_milvus(MILVUS_URI, alias="default", tag="CodeCache")

        dim = self._get_vector_dim()
        if utility.has_collection(CODE_CACHE_COLLECTION):
            current = Collection(CODE_CACHE_COLLECTION)
            if not self._is_schema_compatible(current, dim):
                print(
                    f"⚠️ [CodeCache] Found incompatible schema in '{CODE_CACHE_COLLECTION}', "
                    "dropping and recreating."
                )
                # 自动版本/结构兼容性校验
                utility.drop_collection(CODE_CACHE_COLLECTION)
                current = self._create_collection(dim)
            else:
                current.load()
                print(
                    f"📦 [CodeCache] Reusing collection '{CODE_CACHE_COLLECTION}'")
            self._collection = current
            return self._collection

        self._collection = self._create_collection(dim)
        return self._collection

    def _normalize_url(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            # [Fix] 不再强制只取后两段，而是保留完整 netloc (去除 www.)
            # e.g. mard.gov.vn -> mard.gov.vn, www.google.com -> google.com
            domain = parsed.netloc
            if domain.lower().startswith("www."):
                domain = domain[4:]

            path = re.sub(r"/\d+", "/*", parsed.path or "")
            return f"{domain}{path}"[:512]
        except Exception:
            return (url or "")[:512]

    def _compute_dom_hash(self, dom_skeleton: str) -> str:
        content = (dom_skeleton or "")[:2500]
        return hashlib.md5(content.encode("utf-8")).hexdigest()[:16]

    def _embed_fields(
        self,
        goal: str,
        locator_info: str,
        user_task: str,
        url_pattern: str,
    ) -> Dict[str, List[float]]:
        texts = [
            goal or "",
            locator_info or "",
            user_task or "",
            url_pattern or "",
        ]
        embeddings = self._get_embeddings()
        vectors = embeddings.embed_documents(texts)
        return {
            "goal_vector": vectors[0],
            "locator_vector": vectors[1],
            "user_task_vector": vectors[2],
            "url_vector": vectors[3],
        }

    def _build_ann_requests(self, vectors: Dict[str, List[float]], limit: int) -> List[AnnSearchRequest]:
        params = {"metric_type": "COSINE", "params": {}}
        return [
            AnnSearchRequest(data=[vectors["goal_vector"]],
                             anns_field="goal_vector", param=params, limit=limit),
            AnnSearchRequest(data=[vectors["locator_vector"]],
                             anns_field="locator_vector", param=params, limit=limit),
            AnnSearchRequest(data=[vectors["user_task_vector"]],
                             anns_field="user_task_vector", param=params, limit=limit),
            AnnSearchRequest(data=[vectors["url_vector"]],
                             anns_field="url_vector", param=params, limit=limit),
        ]

    def _to_similarity(self, score: float) -> float:
        value = float(score)
        if 0.0 <= value <= 1.0:
            return value
        if 1.0 < value <= 2.0:
            return max(0.0, 1.0 - value / 2.0)
        if -1.0 <= value < 0.0:
            return max(0.0, min(1.0, 1.0 + value))
        return max(0.0, min(1.0, 1.0 / (1.0 + abs(value))))

    def search(
        self,
        user_task: str,
        goal: str,
        url: str,
        locator_info: str = "",
        top_k: int = 3,
    ) -> List[CacheHit]:
        print("🔎 [CodeCache] Searching for similar code...")
        try:
            collection = self._ensure_collection()
            url_pattern = self._normalize_url(url)
            vectors = self._embed_fields(
                goal=goal, locator_info=locator_info, user_task=user_task, url_pattern=url_pattern)
            ann_limit = max(top_k, 10)
            requests = self._build_ann_requests(vectors, limit=ann_limit)
            ranker = WeightedRanker(*self._weights)

            search_res = hybrid_search(
                collection=collection,
                reqs=requests,
                rerank=ranker,
                limit=top_k,
                output_fields=["cache_id", "code", "url_pattern",
                               "goal", "success_count", "user_task"],
                tag="CodeCache",
            )

            raw_hits = search_res[0] if search_res else []
            hits: List[CacheHit] = []
            for item in raw_hits:
                raw_score = getattr(
                    item, "score", getattr(item, "distance", 0.0))
                sim = self._to_similarity(float(raw_score))
                if sim < self.SIMILARITY_THRESHOLD:
                    continue

                metadata = {
                    "cache_id": read_hit_field(item, "cache_id"),
                    "code": read_hit_field(item, "code"),
                    "url_pattern": read_hit_field(item, "url_pattern"),
                    "goal": read_hit_field(item, "goal"),
                    "success_count": read_hit_field(item, "success_count"),
                    "user_task": read_hit_field(item, "user_task"),
                }

                hits.append(
                    CacheHit(
                        id=metadata.get("cache_id", ""),
                        code=metadata.get("code", ""),
                        score=sim,
                        url_pattern=metadata.get("url_pattern", ""),
                        goal=metadata.get("goal", ""),
                        success_count=int(metadata.get("success_count", 0)),
                        user_task=metadata.get("user_task", ""),
                    )
                )

            if hits:
                print(
                    f"✅ [CodeCache] Found {len(hits)} hits (best score: {hits[0].score:.4f})")
            else:
                print("❌ [CodeCache] No cache hits")
            return hits
        except Exception as exc:
            print(f"⚠️ [CodeCache] Search error: {exc}")
            return []

    def _is_navigation_task(self, goal: str, code: str) -> bool:
        if len(code) > self.NAVIGATION_CODE_MAX_LENGTH:
            return False
        code_lower = code.lower().strip()
        for pattern in ("tab.get(", "tab.get ("):
            if pattern not in code_lower:
                continue
            meaningful_lines = [
                line for line in code.split("\n") if line.strip() and not line.strip().startswith("print")
            ]
            if len(meaningful_lines) <= 3:
                return True
        return False

    def _is_duplicate(self, goal: str, url: str, user_task: str, locator_info: str) -> bool:
        try:
            hits = self.search(
                user_task=user_task,
                goal=goal,
                url=url,
                locator_info=locator_info,
                top_k=1,
            )
            if hits and hits[0].score >= self.DUPLICATE_THRESHOLD:
                print(
                    "   ⚠️ [CodeCache] Similar content already exists "
                    f"(score={hits[0].score:.4f} >= {self.DUPLICATE_THRESHOLD}), skip save"
                )
                return True
            return False
        except Exception as exc:
            print(f"⚠️ [CodeCache] Duplicate check error: {exc}")
            return False

    def _shutdown(self):
        print("📧 [CodeCache] Waiting for background save tasks...")
        self._executor.shutdown(wait=True)
        print("✅ [CodeCache] Background tasks finished")

    def _do_save_async(
        self,
        goal: str,
        dom_skeleton: str,
        url: str,
        code: str,
        user_task: str = "",
        locator_info: str = "",
    ):
        try:
            if self._is_duplicate(goal=goal, url=url, user_task=user_task, locator_info=locator_info):
                return

            collection = self._ensure_collection()
            now = datetime.now().isoformat()
            url_pattern = self._normalize_url(url)
            dom_hash = self._compute_dom_hash(dom_skeleton)
            cache_id = f"{dom_hash}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            vectors = self._embed_fields(
                goal=goal,
                locator_info=locator_info,
                user_task=user_task,
                url_pattern=url_pattern,
            )

            payload = [
                [vectors["goal_vector"]],
                [vectors["locator_vector"]],
                [vectors["user_task_vector"]],
                [vectors["url_vector"]],
                [(goal or "")[:2000]],
                [(locator_info or "")[:6400]],
                [(user_task or "")[:6400]],
                [url_pattern[:512]],
                [(code or "")[:16000]],
                [cache_id],
                [dom_hash],
                [1],
                [0],
                [now],
                [now],
            ]
            insert_and_flush(collection=collection,
                             data=payload, tag="CodeCache")
            print(f"   ✅ [CodeCache] Saved: {cache_id}")
        except Exception as exc:
            print(f"❌ [CodeCache] Background save failed: {exc}")

    def save(
        self,
        goal: str,
        dom_skeleton: str,
        url: str,
        code: str,
        user_task: str = "",
        locator_info: str = "",
    ) -> bool:
        if self._is_navigation_task(goal, code):
            print(
                f"⏭️ [CodeCache] Skip navigation-only code ({len(code)} chars)")
            return False

        if len(code) > self.MAX_CODE_WARN:
            print(
                f"⚠️ [CodeCache] Code is long ({len(code)} chars), "
                "consider splitting task in Planner"
            )

        print(f"📤 [CodeCache] Submit async save (code: {len(code)} chars)")
        self._executor.submit(
            self._do_save_async,
            goal,
            dom_skeleton,
            url,
            code,
            user_task,
            locator_info,
        )
        return True

    def update_stats(self, cache_id: str, success: bool) -> bool:
        action = "success" if success else "fail"
        print(f"📊 [CodeCache] Recording {action} for cache_id: {cache_id}")
        return True


code_cache_manager = CodeCacheManager()
