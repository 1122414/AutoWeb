# ==============================================================================
# Code Cache Manager - 代码缓存复用系统
# ==============================================================================
import hashlib
import re
from datetime import datetime
from typing import Dict, List, NamedTuple, Optional
from urllib.parse import urlparse
import numpy as np

from pymilvus import (
    AnnSearchRequest,
    DataType,
    FieldSchema,
    WeightedRanker,
)

from config import (
    CODE_CACHE_COLLECTION,
    CODE_CACHE_CANDIDATE_TOP_K,
    CODE_CACHE_STAGE2_TASK_MIN_SIM,
    CODE_CACHE_STAGE3_GOAL_MIN_SIM,
    CODE_CACHE_WEIGHT_GOAL,
    CODE_CACHE_WEIGHT_LOCATOR,
    CODE_CACHE_WEIGHT_URL,
    CODE_CACHE_WEIGHT_USER_TASK,
    CODE_CACHE_SIMILARITY_THRESHOLD,
    CODE_CACHE_DUPLICATE_THRESHOLD,
    CODE_CACHE_NAV_MAX_LEN,
    CODE_CACHE_MAX_CODE_WARN,
)
from skills.cache_blacklist import cache_soft_blacklist
from skills.vector_base import VectorCacheBase
from skills.logger import logger
from skills.vector_gateway import (
    hybrid_search,
    insert_and_flush,
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
    locator_info: str = ""
    created_at: str = ""


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


class CodeCacheManager(VectorCacheBase):

    def __init__(self):
        super().__init__(
            weights=(
                CODE_CACHE_WEIGHT_GOAL,
                CODE_CACHE_WEIGHT_LOCATOR,
                CODE_CACHE_WEIGHT_USER_TASK,
                CODE_CACHE_WEIGHT_URL,
            ),
            defaults=(0.6, 0.2, 0.1, 0.1),
            tag="CodeCache",
        )

    # ------------------------------------------------------------------
    # 抽象方法实现
    # ------------------------------------------------------------------

    @property
    def _collection_name(self) -> str:
        return CODE_CACHE_COLLECTION

    @property
    def _collection_description(self) -> str:
        return "AutoWeb code cache with multi-vector hybrid retrieval"

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
            FieldSchema("domain_key", DataType.VARCHAR, max_length=255),
            FieldSchema("code", DataType.VARCHAR, max_length=16000),
            FieldSchema("cache_id", DataType.VARCHAR, max_length=128),
            FieldSchema("dom_hash", DataType.VARCHAR, max_length=64),
            FieldSchema("success_count", DataType.INT64),
            FieldSchema("fail_count", DataType.INT64),
            FieldSchema("created_at", DataType.VARCHAR, max_length=64),
            FieldSchema("updated_at", DataType.VARCHAR, max_length=64),
        ]

    def _vector_field_names(self) -> List[str]:
        return ["goal_vector", "locator_vector", "user_task_vector", "url_vector"]

    # ------------------------------------------------------------------
    # CodeCache 特有逻辑
    # ------------------------------------------------------------------

    def _embed_fields(
        self,
        goal: str,
        locator_info: str,
        user_task: str,
        url_pattern: str,
    ) -> Dict[str, list]:
        texts = [goal or "", locator_info or "",
                 user_task or "", url_pattern or ""]
        vectors = self._get_embeddings().embed_documents(texts)
        return {
            "goal_vector": vectors[0],
            "locator_vector": vectors[1],
            "user_task_vector": vectors[2],
            "url_vector": vectors[3],
        }

    def _build_ann_requests(self, vectors: Dict[str, list], limit: int, expr: str = None) -> List[AnnSearchRequest]:
        params = {"metric_type": "COSINE", "params": {}}
        return [
            AnnSearchRequest(data=[vectors["goal_vector"]],
                             anns_field="goal_vector", param=params, limit=limit, expr=expr),
            AnnSearchRequest(data=[vectors["locator_vector"]],
                             anns_field="locator_vector", param=params, limit=limit, expr=expr),
            AnnSearchRequest(data=[vectors["user_task_vector"]],
                             anns_field="user_task_vector", param=params, limit=limit, expr=expr),
            AnnSearchRequest(data=[vectors["url_vector"]],
                             anns_field="url_vector", param=params, limit=limit, expr=expr),
        ]

    def _build_stage1_requests(self, vectors: Dict[str, list], limit: int, expr: str = None) -> List[AnnSearchRequest]:
        params = {"metric_type": "COSINE", "params": {}}
        return [
            AnnSearchRequest(data=[vectors["user_task_vector"]],
                             anns_field="user_task_vector", param=params, limit=limit, expr=expr),
            AnnSearchRequest(data=[vectors["url_vector"]],
                             anns_field="url_vector", param=params, limit=limit, expr=expr),
        ]

    def _build_goal_request(self, vectors: Dict[str, list], limit: int, expr: str = None) -> List[AnnSearchRequest]:
        params = {"metric_type": "COSINE", "params": {}}
        return [
            AnnSearchRequest(data=[vectors["goal_vector"]],
                             anns_field="goal_vector", param=params, limit=limit, expr=expr),
        ]

    def _cosine_similarity(self, a: list, b: list) -> float:
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
        goal: str,
        url: str,
        locator_info: str = "",
        top_k: int = 3,
    ) -> List[CacheHit]:
        logger.info("🔎 [CodeCache] Searching for similar code...")
        try:
            collection = self._ensure_collection()
            url_pattern = self._normalize_url(url)
            domain_key = self._extract_domain_key(url)
            if not domain_key:
                logger.info("⏭️ [CodeCache] Skip search: empty domain_key")
                return []

            vectors = self._embed_fields(
                goal=goal, locator_info=locator_info, user_task=user_task, url_pattern=url_pattern)
            ann_limit = max(top_k, CODE_CACHE_CANDIDATE_TOP_K)
            base_expr = self._build_domain_expr(domain_key)

            stage1_res = hybrid_search(
                collection=collection,
                reqs=self._build_stage1_requests(
                    vectors, limit=ann_limit, expr=base_expr),
                rerank=WeightedRanker(0.85, 0.15),
                limit=ann_limit,
                expr=base_expr,
                output_fields=[
                    "cache_id", "code", "url_pattern", "domain_key", "goal",
                    "success_count", "user_task", "locator_info", "created_at"
                ],
                tag="CodeCache",
            )

            stage2_items = []
            query_task_vec = vectors["user_task_vector"]
            for item in (stage1_res[0] if stage1_res else []):
                hit_user_task = (read_hit_field(
                    item, "user_task") or "").strip()
                hit_user_vec = self._get_embeddings().embed_query(hit_user_task or "")
                task_sim = self._cosine_similarity(
                    query_task_vec, hit_user_vec)
                if task_sim < CODE_CACHE_STAGE2_TASK_MIN_SIM:
                    continue
                stage2_items.append(item)

            candidate_ids = [
                (read_hit_field(item, "cache_id") or "") for item in stage2_items
            ]
            candidate_ids = [x for x in candidate_ids if x][:ann_limit]
            if not candidate_ids:
                logger.info("❌ [CodeCache] No stage2 candidates")
                return []

            stage3_expr = self._build_cache_id_expr(
                candidate_ids, base_expr=base_expr)
            stage3_res = hybrid_search(
                collection=collection,
                reqs=self._build_goal_request(
                    vectors, limit=ann_limit, expr=stage3_expr),
                rerank=WeightedRanker(1.0),
                limit=ann_limit,
                expr=stage3_expr,
                output_fields=[
                    "cache_id", "code", "url_pattern", "domain_key", "goal",
                    "success_count", "user_task", "locator_info", "created_at"
                ],
                tag="CodeCache",
            )

            raw_hits = stage3_res[0] if stage3_res else []
            hits: List[CacheHit] = []
            for item in raw_hits:
                raw_score = getattr(
                    item, "score", getattr(item, "distance", 0.0))
                sim = self._to_similarity(float(raw_score))
                if sim < max(CODE_CACHE_SIMILARITY_THRESHOLD, CODE_CACHE_STAGE3_GOAL_MIN_SIM):
                    continue

                metadata = {
                    "cache_id": read_hit_field(item, "cache_id"),
                    "code": read_hit_field(item, "code"),
                    "url_pattern": read_hit_field(item, "url_pattern"),
                    "goal": read_hit_field(item, "goal"),
                    "success_count": read_hit_field(item, "success_count"),
                    "user_task": read_hit_field(item, "user_task"),
                    "locator_info": read_hit_field(item, "locator_info"),
                    "created_at": read_hit_field(item, "created_at"),
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
                        locator_info=metadata.get("locator_info", ""),
                        created_at=metadata.get("created_at", ""),
                    )
                )

            if hits:
                allowed_ids = set(
                    cache_soft_blacklist.filter_allowed_ids(
                        cache_type="codecache",
                        domain_key=domain_key,
                        cache_ids=[h.id for h in hits if h.id],
                    )
                )
                if allowed_ids:
                    hits = [h for h in hits if (
                        not h.id) or (h.id in allowed_ids)]
                else:
                    hits = [h for h in hits if not h.id]

            if hits:
                logger.info(
                    f"✅ [CodeCache] Found {len(hits)} hits (best score: {hits[0].score:.4f})")
            else:
                logger.info("❌ [CodeCache] No cache hits")
            return hits
        except Exception as exc:
            logger.warning(f"⚠️ [CodeCache] Search error: {exc}")
            return []

    def _is_navigation_task(self, goal: str, code: str) -> bool:
        if len(code) > CODE_CACHE_NAV_MAX_LEN:
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

    def _search_duplicate_hit(
        self,
        goal: str,
        url: str,
        user_task: str,
        locator_info: str,
    ) -> Optional[CacheHit]:
        collection = self._ensure_collection()
        url_pattern = self._normalize_url(url)
        domain_key = self._extract_domain_key(url)
        if not domain_key:
            return None

        vectors = self._embed_fields(
            goal=goal,
            locator_info=locator_info,
            user_task=user_task,
            url_pattern=url_pattern,
        )
        base_expr = self._build_domain_expr(domain_key)
        res = hybrid_search(
            collection=collection,
            reqs=self._build_ann_requests(vectors, limit=1, expr=base_expr),
            rerank=WeightedRanker(*self._weights),
            limit=1,
            expr=base_expr,
            output_fields=[
                "cache_id", "code", "url_pattern", "domain_key", "goal",
                "success_count", "user_task", "locator_info", "created_at"
            ],
            tag="CodeCache",
        )

        items = res[0] if res else []
        if not items:
            return None

        item = items[0]
        raw_score = getattr(item, "score", getattr(item, "distance", 0.0))
        sim = self._to_similarity(float(raw_score))
        hit = CacheHit(
            id=(read_hit_field(item, "cache_id") or ""),
            code=(read_hit_field(item, "code") or ""),
            score=sim,
            url_pattern=(read_hit_field(item, "url_pattern") or ""),
            goal=(read_hit_field(item, "goal") or ""),
            success_count=int(read_hit_field(item, "success_count") or 0),
            user_task=(read_hit_field(item, "user_task") or ""),
            locator_info=(read_hit_field(item, "locator_info") or ""),
            created_at=(read_hit_field(item, "created_at") or ""),
        )

        if hit.id:
            allowed_ids = set(
                cache_soft_blacklist.filter_allowed_ids(
                    cache_type="codecache",
                    domain_key=domain_key,
                    cache_ids=[hit.id],
                )
            )
            if hit.id not in allowed_ids:
                return None
        return hit

    def _is_duplicate(self, goal: str, url: str, user_task: str, locator_info: str) -> bool:
        try:
            hit = self._search_duplicate_hit(
                goal=goal,
                url=url,
                user_task=user_task,
                locator_info=locator_info,
            )
            if hit and hit.score >= CODE_CACHE_DUPLICATE_THRESHOLD:
                logger.info(
                    "   ⚠️ [CodeCache] Similar content already exists "
                    f"(score={hit.score:.4f} >= {CODE_CACHE_DUPLICATE_THRESHOLD}), skip save"
                )
                return True
            return False
        except Exception as exc:
            logger.warning(f"⚠️ [CodeCache] Duplicate check error: {exc}")
            return False

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
                [self._extract_domain_key(url)[:255]],
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
            logger.info(f"   ✅ [CodeCache] Saved: {cache_id}")
        except Exception as exc:
            logger.error(f"❌ [CodeCache] Background save failed: {exc}")

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
            logger.info(
                f"⏭️ [CodeCache] Skip navigation-only code ({len(code)} chars)")
            return False

        if len(code) > CODE_CACHE_MAX_CODE_WARN:
            logger.warning(
                f"⚠️ [CodeCache] Code is long ({len(code)} chars), "
                "consider splitting task in Planner"
            )

        logger.info(
            f"📤 [CodeCache] Submit async save (code: {len(code)} chars)")
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


code_cache_manager = CodeCacheManager()
