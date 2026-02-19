# ==============================================================================
# DOM Cache Manager - DOM 结构缓存系统
# ==============================================================================
import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, NamedTuple, Optional
from skills.logger import logger

import numpy as np
from pymilvus import (
    AnnSearchRequest,
    DataType,
    FieldSchema,
    WeightedRanker,
)

from config import (
    DOM_CACHE_COLLECTION,
    DOM_CACHE_TASK_MIN_SIM,
    DOM_CACHE_TTL_HOURS,
    DOM_CACHE_WEIGHT_DOM,
    DOM_CACHE_WEIGHT_TASK,
    DOM_CACHE_WEIGHT_URL,
)
from skills.vector_base import VectorCacheBase
from skills.vector_gateway import (
    filter_not_expired,
    hybrid_search,
    insert_and_flush,
    read_hit_field,
)


class DomCacheHit(NamedTuple):
    id: str
    score: float
    locator_suggestions: List[Dict]
    url_pattern: str
    dom_hash: str
    task_intent: str


class DomCacheManager(VectorCacheBase):
    DOM_TEXT_MAX = 12000
    TASK_TEXT_MAX = 1500

    def __init__(self):
        super().__init__(
            weights=(DOM_CACHE_WEIGHT_URL, DOM_CACHE_WEIGHT_DOM,
                     DOM_CACHE_WEIGHT_TASK),
            defaults=(0.2, 0.7, 0.1),
            tag="DomCache",
        )

    # ------------------------------------------------------------------
    # 抽象方法实现
    # ------------------------------------------------------------------

    @property
    def _collection_name(self) -> str:
        return DOM_CACHE_COLLECTION

    @property
    def _collection_description(self) -> str:
        return "Observer DOM cache with hybrid vectors"

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
            FieldSchema("locator_suggestions",
                        DataType.VARCHAR, max_length=65535),
            FieldSchema("created_at", DataType.VARCHAR, max_length=32),
            FieldSchema("updated_at", DataType.VARCHAR, max_length=32),
            FieldSchema("expire_at", DataType.VARCHAR, max_length=32),
            FieldSchema("hit_count", DataType.INT64),
            FieldSchema("fail_count", DataType.INT64),
        ]

    def _vector_field_names(self) -> List[str]:
        return ["url_vector", "dom_vector", "task_vector"]

    # ------------------------------------------------------------------
    # DomCache 特有逻辑
    # ------------------------------------------------------------------

    def _compact_dom(self, dom_skeleton: str) -> str:
        if not dom_skeleton:
            return ""
        dom = re.sub(r"\s+", " ", dom_skeleton)
        dom = re.sub(r"\b\d+\b", "0", dom)
        return dom[: self.DOM_TEXT_MAX]

    def _task_intent(self, user_task: str) -> str:
        text = re.sub(r"\s+", " ", (user_task or "").strip())
        return text[: self.TASK_TEXT_MAX]

    def _embed_fields(self, url_pattern: str, dom_skeleton: str, task_intent: str) -> Dict[str, list]:
        texts = [url_pattern or "", self._compact_dom(
            dom_skeleton), task_intent or ""]
        vectors = self._get_embeddings().embed_documents(texts)
        return {"url_vector": vectors[0], "dom_vector": vectors[1], "task_vector": vectors[2]}

    def _build_requests(self, vectors: Dict[str, list], limit: int) -> List[AnnSearchRequest]:
        params = {"metric_type": "COSINE", "params": {}}
        return [
            AnnSearchRequest(data=[vectors["url_vector"]],
                             anns_field="url_vector", param=params, limit=limit),
            AnnSearchRequest(data=[vectors["dom_vector"]],
                             anns_field="dom_vector", param=params, limit=limit),
            AnnSearchRequest(data=[vectors["task_vector"]],
                             anns_field="task_vector", param=params, limit=limit),
        ]

    def _decode_locator_suggestions(self, raw: str) -> List[Dict]:
        try:
            val = json.loads(raw) if raw else []
            return val if isinstance(val, list) else []
        except Exception:
            return []

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
        current_url: str,
        dom_skeleton: str,
        top_k: int = 3,
    ) -> List[DomCacheHit]:
        try:
            collection = self._ensure_collection()
            now_dt = datetime.now()
            url_pattern = self._normalize_url(current_url)
            task_intent = self._task_intent(user_task)
            vectors = self._embed_fields(
                url_pattern, dom_skeleton, task_intent)

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
                score = float(
                    getattr(item, "score", getattr(item, "distance", 0.0)))
                locator_raw = read_hit_field(
                    item, "locator_suggestions") or "[]"
                hit_task_intent = (read_hit_field(
                    item, "task_intent") or "").strip()
                # Hard gate: task intent similarity must pass threshold
                task_vec = self._get_embeddings().embed_query(hit_task_intent or "")
                task_sim = self._cosine_similarity(query_task_vec, task_vec)
                if task_sim < DOM_CACHE_TASK_MIN_SIM:
                    logger.info(
                        f"⏭️ [DomCache] Skip hit by task gate: sim={task_sim:.4f} "
                        f"< min={DOM_CACHE_TASK_MIN_SIM:.2f}"
                    )
                    continue
                hits.append(
                    DomCacheHit(
                        id=(read_hit_field(item, "cache_id") or ""),
                        score=score,
                        locator_suggestions=self._decode_locator_suggestions(
                            locator_raw),
                        url_pattern=(read_hit_field(
                            item, "url_pattern") or ""),
                        dom_hash=(read_hit_field(item, "dom_hash") or ""),
                        task_intent=(read_hit_field(
                            item, "task_intent") or ""),
                    )
                )
            return hits[:top_k]
        except Exception as exc:
            logger.warning(f"⚠️ [DomCache] Search error: {exc}")
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
            exp_iso = (now + timedelta(hours=max(1, DOM_CACHE_TTL_HOURS))
                       ).strftime("%Y-%m-%dT%H:%M:%S")

            url_pattern = self._normalize_url(current_url)
            task_intent = self._task_intent(user_task)
            dom_hash = self._compute_dom_hash(dom_skeleton)
            cache_id = f"{dom_hash}_{now.strftime('%Y%m%d%H%M%S')}"
            vectors = self._embed_fields(
                url_pattern, dom_skeleton, task_intent)

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
            insert_and_flush(collection=collection,
                             data=payload, tag="DomCache")
            logger.info(
                f"✅ [DomCache] Saved cache_id={cache_id}, url={url_pattern}, "
                f"ttl_hours={max(1, DOM_CACHE_TTL_HOURS)}"
            )
        except Exception as exc:
            logger.error(f"❌ [DomCache] Save failed: {exc}")

    def save(
        self,
        user_task: str,
        current_url: str,
        dom_skeleton: str,
        locator_suggestions: List[Dict],
    ) -> bool:
        if not locator_suggestions:
            logger.info("⏭️ [DomCache] Skip save: empty locator_suggestions")
            return False
        logger.info(
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


dom_cache_manager = DomCacheManager()
