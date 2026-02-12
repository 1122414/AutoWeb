import atexit
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Dict, List, NamedTuple, Optional, Tuple
from urllib.parse import urlparse

import numpy as np
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
    DOM_CACHE_COLLECTION,
    DOM_CACHE_TASK_MIN_SIM,
    DOM_CACHE_TTL_HOURS,
    DOM_CACHE_WEIGHT_DOM,
    DOM_CACHE_WEIGHT_TASK,
    DOM_CACHE_WEIGHT_URL,
    MILVUS_URI,
)
from skills.vector_gateway import (
    connect_milvus,
    filter_not_expired,
    hybrid_search,
    insert_and_flush,
    normalize_weights,
    read_hit_field,
)


class DomCacheHit(NamedTuple):
    id: str
    score: float
    locator_suggestions: List[Dict]
    url_pattern: str
    dom_hash: str
    task_intent: str


class DomCacheManager:
    DOM_TEXT_MAX = 12000
    TASK_TEXT_MAX = 1500

    def __init__(self):
        self._collection: Optional[Collection] = None
        self._embeddings = None
        self._vector_dim: Optional[int] = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="DomCache")
        self._weights = normalize_weights(
            (DOM_CACHE_WEIGHT_URL, DOM_CACHE_WEIGHT_DOM, DOM_CACHE_WEIGHT_TASK),
            defaults=(0.2, 0.7, 0.1),
            tag="DomCache",
        )
        atexit.register(self._shutdown)

    def _shutdown(self):
        self._executor.shutdown(wait=True)

    def _get_embeddings(self):
        if self._embeddings is None:
            from rag.retriever_qa import get_embedding_model

            self._embeddings = get_embedding_model()
        return self._embeddings

    def _get_vector_dim(self) -> int:
        if self._vector_dim is None:
            vec = self._get_embeddings().embed_query("dom_cache_dim_probe")
            self._vector_dim = len(vec)
        return self._vector_dim

    def _schema_fields(self, dim: int) -> List[FieldSchema]:
        return [
            FieldSchema("pk", DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema("url_vector", DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema("dom_vector", DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema("task_vector", DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema("cache_id", DataType.VARCHAR, max_length=128),
            FieldSchema("url_pattern", DataType.VARCHAR, max_length=512),
            FieldSchema("task_intent", DataType.VARCHAR, max_length=2000),
            FieldSchema("dom_hash", DataType.VARCHAR, max_length=64),
            FieldSchema("locator_suggestions", DataType.VARCHAR, max_length=65535),
            FieldSchema("created_at", DataType.VARCHAR, max_length=32),
            FieldSchema("updated_at", DataType.VARCHAR, max_length=32),
            FieldSchema("expire_at", DataType.VARCHAR, max_length=32),
            FieldSchema("hit_count", DataType.INT64),
            FieldSchema("fail_count", DataType.INT64),
        ]

    def _is_schema_compatible(self, collection: Collection, dim: int) -> bool:
        required = {
            "url_vector",
            "dom_vector",
            "task_vector",
            "cache_id",
            "url_pattern",
            "task_intent",
            "dom_hash",
            "locator_suggestions",
            "created_at",
            "updated_at",
            "expire_at",
            "hit_count",
            "fail_count",
        }
        fields = {f.name: f for f in collection.schema.fields}
        if not required.issubset(fields.keys()):
            return False
        for name in ("url_vector", "dom_vector", "task_vector"):
            field = fields[name]
            if field.dtype != DataType.FLOAT_VECTOR:
                return False
            if int(field.params.get("dim", -1)) != dim:
                return False
        return True

    def _create_collection(self, dim: int) -> Collection:
        collection = Collection(
            name=DOM_CACHE_COLLECTION,
            schema=CollectionSchema(
                fields=self._schema_fields(dim),
                description="Observer DOM cache with hybrid vectors",
                enable_dynamic_field=True,
            ),
            consistency_level="Bounded",
        )
        vec_idx = {"metric_type": "COSINE", "index_type": "AUTOINDEX", "params": {}}
        collection.create_index(field_name="url_vector", index_params=vec_idx)
        collection.create_index(field_name="dom_vector", index_params=vec_idx)
        collection.create_index(field_name="task_vector", index_params=vec_idx)
        collection.create_index(field_name="url_pattern", index_params={"index_type": "INVERTED"})
        collection.create_index(field_name="dom_hash", index_params={"index_type": "INVERTED"})
        collection.load()
        print(f"✅ [DomCache] Created collection '{DOM_CACHE_COLLECTION}' (dim={dim})")
        return collection

    def _ensure_collection(self) -> Collection:
        if self._collection is not None:
            return self._collection
        connect_milvus(MILVUS_URI, alias="default", tag="DomCache")
        dim = self._get_vector_dim()

        if utility.has_collection(DOM_CACHE_COLLECTION):
            current = Collection(DOM_CACHE_COLLECTION)
            if not self._is_schema_compatible(current, dim):
                print(
                    f"⚠️ [DomCache] Incompatible schema in '{DOM_CACHE_COLLECTION}', dropping and recreating."
                )
                utility.drop_collection(DOM_CACHE_COLLECTION)
                current = self._create_collection(dim)
            else:
                current.load()
            self._collection = current
            return self._collection

        self._collection = self._create_collection(dim)
        return self._collection

    def _normalize_url(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            domain_parts = parsed.netloc.split(".")
            domain = ".".join(domain_parts[-2:]) if len(domain_parts) >= 2 else parsed.netloc
            path = re.sub(r"/\d+", "/*", parsed.path or "")
            return f"{domain}{path}"[:512]
        except Exception:
            return (url or "")[:512]

    def _compact_dom(self, dom_skeleton: str) -> str:
        if not dom_skeleton:
            return ""
        dom = re.sub(r"\s+", " ", dom_skeleton)
        dom = re.sub(r"\b\d+\b", "0", dom)
        return dom[: self.DOM_TEXT_MAX]

    def _task_intent(self, user_task: str) -> str:
        text = re.sub(r"\s+", " ", (user_task or "").strip())
        return text[: self.TASK_TEXT_MAX]

    def _compute_dom_hash(self, dom_skeleton: str) -> str:
        compact = self._compact_dom(dom_skeleton)
        return hashlib.md5(compact.encode("utf-8")).hexdigest()[:16]

    def _embed_fields(self, url_pattern: str, dom_skeleton: str, task_intent: str) -> Dict[str, List[float]]:
        texts = [url_pattern or "", self._compact_dom(dom_skeleton), task_intent or ""]
        vectors = self._get_embeddings().embed_documents(texts)
        return {"url_vector": vectors[0], "dom_vector": vectors[1], "task_vector": vectors[2]}

    def _build_requests(self, vectors: Dict[str, List[float]], limit: int) -> List[AnnSearchRequest]:
        params = {"metric_type": "COSINE", "params": {}}
        return [
            AnnSearchRequest(data=[vectors["url_vector"]], anns_field="url_vector", param=params, limit=limit),
            AnnSearchRequest(data=[vectors["dom_vector"]], anns_field="dom_vector", param=params, limit=limit),
            AnnSearchRequest(data=[vectors["task_vector"]], anns_field="task_vector", param=params, limit=limit),
        ]

    def _decode_locator_suggestions(self, raw: str) -> List[Dict]:
        try:
            val = json.loads(raw) if raw else []
            return val if isinstance(val, list) else []
        except Exception:
            return []

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        va = np.asarray(a, dtype=np.float32)
        vb = np.asarray(b, dtype=np.float32)
        denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
        if denom <= 0.0:
            return 0.0
        return float(np.dot(va, vb) / denom)

    def search(
        self,
        user_task: str,
        current_url: str,
        dom_skeleton: str,
        top_k: int = 3,
    ) -> List[DomCacheHit]:
        try:
            collection = self._ensure_collection()
            now_dt = datetime.now()
            url_pattern = self._normalize_url(current_url)
            task_intent = self._task_intent(user_task)
            vectors = self._embed_fields(url_pattern, dom_skeleton, task_intent)

            requests = self._build_requests(vectors, max(top_k, 8))
            ranker = WeightedRanker(*self._weights)
            res = hybrid_search(
                collection=collection,
                reqs=requests,
                rerank=ranker,
                limit=max(top_k, 8),
                output_fields=[
                    "cache_id",
                    "url_pattern",
                    "dom_hash",
                    "task_intent",
                    "locator_suggestions",
                    "expire_at",
                ],
                tag="DomCache",
            )

            hits = []
            query_task_vec = vectors["task_vector"]
            raw_hits = filter_not_expired(
                hits=(res[0] if res else []),
                expire_field="expire_at",
                now_dt=now_dt,
                tag="DomCache",
            )
            for item in raw_hits:
                score = float(getattr(item, "score", getattr(item, "distance", 0.0)))
                locator_raw = read_hit_field(item, "locator_suggestions") or "[]"
                hit_task_intent = (read_hit_field(item, "task_intent") or "").strip()
                # Hard gate: task intent similarity must pass threshold, even if hybrid score is high.
                task_vec = self._get_embeddings().embed_query(hit_task_intent or "")
                task_sim = self._cosine_similarity(query_task_vec, task_vec)
                if task_sim < DOM_CACHE_TASK_MIN_SIM:
                    print(
                        f"⏭️ [DomCache] Skip hit by task gate: sim={task_sim:.4f} "
                        f"< min={DOM_CACHE_TASK_MIN_SIM:.2f}"
                    )
                    continue
                hits.append(
                    DomCacheHit(
                        id=(read_hit_field(item, "cache_id") or ""),
                        score=score,
                        locator_suggestions=self._decode_locator_suggestions(locator_raw),
                        url_pattern=(read_hit_field(item, "url_pattern") or ""),
                        dom_hash=(read_hit_field(item, "dom_hash") or ""),
                        task_intent=(read_hit_field(item, "task_intent") or ""),
                    )
                )
            return hits[:top_k]
        except Exception as exc:
            print(f"⚠️ [DomCache] Search error: {exc}")
            return []

    def _do_save_async(
        self,
        user_task: str,
        current_url: str,
        dom_skeleton: str,
        locator_suggestions: List[Dict],
    ):
        try:
            collection = self._ensure_collection()
            now = datetime.now()
            now_iso = now.strftime("%Y-%m-%dT%H:%M:%S")
            exp_iso = (now + timedelta(hours=max(1, DOM_CACHE_TTL_HOURS))).strftime("%Y-%m-%dT%H:%M:%S")

            url_pattern = self._normalize_url(current_url)
            task_intent = self._task_intent(user_task)
            dom_hash = self._compute_dom_hash(dom_skeleton)
            cache_id = f"{dom_hash}_{now.strftime('%Y%m%d%H%M%S')}"
            vectors = self._embed_fields(url_pattern, dom_skeleton, task_intent)

            # pymilvus insert 使用“按列”格式:
            # 外层 list 是字段列，内层 list 是该字段这一批次的值（这里每列都只有 1 个值，即插入 1 行）。
            # 顺序必须与 schema 字段顺序一致:
            # url_vector, dom_vector, task_vector, cache_id, url_pattern, task_intent,
            # dom_hash, locator_suggestions, created_at, updated_at, expire_at, hit_count, fail_count
            # 其中 created_at/updated_at 初始都用 now_iso；hit_count/fail_count 初始都为 0。
            payload = [
                [vectors["url_vector"]],
                [vectors["dom_vector"]],
                [vectors["task_vector"]],
                [cache_id],
                [url_pattern[:512]],
                [task_intent[:2000]],
                [dom_hash],
                [json.dumps(locator_suggestions, ensure_ascii=False)[:65535]],
                [now_iso],
                [now_iso],
                [exp_iso],
                [0],
                [0],
            ]
            insert_and_flush(collection=collection, data=payload, tag="DomCache")
            print(
                f"✅ [DomCache] Saved cache_id={cache_id}, url={url_pattern}, "
                f"ttl_hours={max(1, DOM_CACHE_TTL_HOURS)}"
            )
        except Exception as exc:
            print(f"❌ [DomCache] Save failed: {exc}")

    def save(
        self,
        user_task: str,
        current_url: str,
        dom_skeleton: str,
        locator_suggestions: List[Dict],
    ) -> bool:
        if not locator_suggestions:
            print("⏭️ [DomCache] Skip save: empty locator_suggestions")
            return False
        print(
            f"📤 [DomCache] Submit async save, url={self._normalize_url(current_url)}, "
            f"task_len={len(user_task or '')}"
        )
        self._executor.submit(
            self._do_save_async,
            user_task,
            current_url,
            dom_skeleton,
            locator_suggestions,
        )
        return True

    def invalidate(self, cache_id: str) -> bool:
        if not cache_id:
            return False
        try:
            collection = self._ensure_collection()
            safe = cache_id.replace('"', '\\"')
            collection.delete(expr=f'cache_id == "{safe}"')
            return True
        except Exception as exc:
            print(f"⚠️ [DomCache] Invalidate error: {exc}")
            return False


dom_cache_manager = DomCacheManager()
