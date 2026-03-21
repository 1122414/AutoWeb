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
    DOM_CACHE_CANDIDATE_TOP_K,
    DOM_CACHE_DUPLICATE_THRESHOLD,
    DOM_CACHE_STAGE2_TASK_MIN_SIM,
    DOM_CACHE_STAGE3_SCORE_THRESHOLD,
    DOM_CACHE_STAGE3_WEIGHT_DOM,
    DOM_CACHE_STAGE3_WEIGHT_STEP,
    DOM_CACHE_STEP_TEXT_MAX,
    DOM_CACHE_TOP_K,
    DOM_CACHE_TTL_HOURS,
    DOM_CACHE_WEIGHT_DOM,
    DOM_CACHE_WEIGHT_STEP,
    DOM_CACHE_WEIGHT_TASK,
    DOM_CACHE_WEIGHT_URL,
)
from skills.cache_blacklist import cache_soft_blacklist
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
    step_context: str = ""
    created_at: str = ""


class DomCacheManager(VectorCacheBase):
    DOM_TEXT_MAX = 12000
    TASK_TEXT_MAX = 1500
    STEP_TEXT_MAX = DOM_CACHE_STEP_TEXT_MAX

    def __init__(self):
        super().__init__(
            weights=(DOM_CACHE_WEIGHT_URL, DOM_CACHE_WEIGHT_DOM,
                     DOM_CACHE_WEIGHT_TASK, DOM_CACHE_WEIGHT_STEP),
            defaults=(0.2, 0.45, 0.2, 0.15),
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
            FieldSchema("step_vector", DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema("cache_id", DataType.VARCHAR, max_length=128),
            FieldSchema("url_pattern", DataType.VARCHAR, max_length=512),
            FieldSchema("domain_key", DataType.VARCHAR, max_length=255),
            FieldSchema("task_intent", DataType.VARCHAR, max_length=2000),
            FieldSchema("step_context", DataType.VARCHAR, max_length=2000),
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
        return ["url_vector", "dom_vector", "task_vector", "step_vector"]

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

    def _step_context(self, step_context: str) -> str:
        text = re.sub(r"\s+", " ", (step_context or "").strip())
        return text[: self.STEP_TEXT_MAX]

    def _embed_fields(
        self,
        url_pattern: str,
        dom_skeleton: str,
        task_intent: str,
        step_context: str,
    ) -> Dict[str, list]:
        texts = [
            url_pattern or "",
            self._compact_dom(dom_skeleton),
            task_intent or "",
            step_context or "",
        ]
        vectors = self._get_embeddings().embed_documents(texts)
        return {
            "url_vector": vectors[0],
            "dom_vector": vectors[1],
            "task_vector": vectors[2],
            "step_vector": vectors[3],
        }

    def _build_task_request(self, vectors: Dict[str, list], limit: int, expr: str = None) -> List[AnnSearchRequest]:
        return self._build_ann_requests_for_fields(
            vectors,
            ["task_vector"],
            limit,
            expr
        )

    def _build_stage3_requests(self, vectors: Dict[str, list], limit: int, expr: str = None) -> List[AnnSearchRequest]:
        return self._build_ann_requests_for_fields(
            vectors,
            ["dom_vector", "step_vector"],
            limit,
            expr
        )

    def _decode_locator_suggestions(self, raw: str) -> List[Dict]:
        try:
            val = json.loads(raw) if raw else []
            return val if isinstance(val, list) else []
        except Exception:
            return []

    def search(
        self,
        user_task: str,
        current_url: str,
        dom_skeleton: str,
        step_context: str = "",
        top_k: int = 3,
    ) -> List[DomCacheHit]:
        try:
            collection = self._ensure_collection()
            now_dt = datetime.now()
            url_pattern = self._normalize_url(current_url)
            domain_key = self._extract_domain_key(current_url)
            if not domain_key:
                logger.info("⏭️ [DomCache] Skip search: empty domain_key")
                return []

            task_intent = self._task_intent(user_task)
            step_text = self._step_context(step_context)
            vectors = self._embed_fields(
                url_pattern=url_pattern,
                dom_skeleton=dom_skeleton,
                task_intent=task_intent,
                step_context=step_text,
            )

            candidate_limit = max(top_k, DOM_CACHE_TOP_K,
                                  DOM_CACHE_CANDIDATE_TOP_K)
            base_expr = self._build_domain_expr(domain_key)

            stage2_res = hybrid_search(
                collection=collection,
                reqs=self._build_task_request(
                    vectors, candidate_limit, expr=base_expr),
                rerank=WeightedRanker(1.0),
                limit=candidate_limit,
                output_fields=[
                    "cache_id",
                    "url_pattern",
                    "domain_key",
                    "dom_hash",
                    "task_intent",
                    "step_context",
                    "locator_suggestions",
                    "created_at",
                    "expire_at",
                ],
                expr=base_expr,
                tag="DomCache",
            )

            stage2_hits = []
            query_task_vec = vectors["task_vector"]
            raw_stage2 = filter_not_expired(
                hits=(stage2_res[0] if stage2_res else []),
                expire_field="expire_at",
                now_dt=now_dt,
                tag="DomCache",
            )
            for item in raw_stage2:
                hit_task_intent = (read_hit_field(
                    item, "task_intent") or "").strip()
                task_vec = self._get_embeddings().embed_query(hit_task_intent or "")
                task_sim = self._cosine_similarity(query_task_vec, task_vec)
                if task_sim < DOM_CACHE_STAGE2_TASK_MIN_SIM:
                    logger.info(
                        f"⏭️ [DomCache] Skip hit by task gate: sim={task_sim:.4f} "
                        f"< min={DOM_CACHE_STAGE2_TASK_MIN_SIM:.2f}"
                    )
                    continue
                stage2_hits.append(item)

            candidate_ids = [
                (read_hit_field(x, "cache_id") or "") for x in stage2_hits
            ]
            candidate_ids = [x for x in candidate_ids if x][:candidate_limit]
            if not candidate_ids:
                return []

            stage3_expr = self._build_cache_id_expr(
                candidate_ids, base_expr=base_expr)
            w_dom = max(0.0, float(DOM_CACHE_STAGE3_WEIGHT_DOM))
            w_step = max(0.0, float(DOM_CACHE_STAGE3_WEIGHT_STEP))
            total = w_dom + w_step
            if total <= 0:
                w_dom, w_step = 0.65, 0.35
            else:
                w_dom, w_step = (w_dom / total), (w_step / total)

            stage3_res = hybrid_search(
                collection=collection,
                reqs=self._build_stage3_requests(
                    vectors, candidate_limit, expr=stage3_expr),
                rerank=WeightedRanker(w_dom, w_step),
                limit=candidate_limit,
                output_fields=[
                    "cache_id",
                    "url_pattern",
                    "domain_key",
                    "dom_hash",
                    "task_intent",
                    "step_context",
                    "locator_suggestions",
                    "created_at",
                    "expire_at",
                ],
                expr=stage3_expr,
                tag="DomCache",
            )

            hits: List[DomCacheHit] = []
            raw_stage3 = filter_not_expired(
                hits=(stage3_res[0] if stage3_res else []),
                expire_field="expire_at",
                now_dt=now_dt,
                tag="DomCache",
            )
            for item in raw_stage3:
                raw_score = float(
                    getattr(item, "score", getattr(item, "distance", 0.0)))
                score = self._to_similarity(raw_score)
                if score < DOM_CACHE_STAGE3_SCORE_THRESHOLD:
                    continue
                locator_raw = read_hit_field(
                    item, "locator_suggestions") or "[]"
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
                        step_context=(read_hit_field(
                            item, "step_context") or ""),
                        created_at=(read_hit_field(item, "created_at") or ""),
                    )
                )

            allowed_ids = set(
                cache_soft_blacklist.filter_allowed_ids(
                    cache_type="domcache",
                    domain_key=domain_key,
                    cache_ids=[h.id for h in hits if h.id],
                )
            )
            if allowed_ids:
                hits = [h for h in hits if (not h.id) or (h.id in allowed_ids)]
            else:
                hits = [h for h in hits if not h.id]
            return hits[:top_k]
        except Exception as exc:
            logger.warning(f"⚠️ [DomCache] Search error: {exc}")
            return []

    def _is_duplicate(
        self,
        user_task: str,
        current_url: str,
        dom_skeleton: str,
        step_context: str,
    ) -> bool:
        try:
            hits = self.search(
                user_task=user_task,
                current_url=current_url,
                dom_skeleton=dom_skeleton,
                step_context=step_context,
                top_k=1,
            )
            if hits and hits[0].score >= DOM_CACHE_DUPLICATE_THRESHOLD:
                logger.info(
                    "⏭️ [DomCache] Similar content already exists "
                    f"(score={hits[0].score:.4f} >= {DOM_CACHE_DUPLICATE_THRESHOLD}), skip save"
                )
                return True
            return False
        except Exception as exc:
            logger.warning(f"⚠️ [DomCache] Duplicate check error: {exc}")
            return False

    def _do_save_async(
        self,
        user_task: str,
        current_url: str,
        dom_skeleton: str,
        step_context: str,
        locator_suggestions: List[Dict],
    ):
        try:
            if self._is_duplicate(
                user_task=user_task,
                current_url=current_url,
                dom_skeleton=dom_skeleton,
                step_context=step_context,
            ):
                return

            collection = self._ensure_collection()
            now = datetime.now()
            now_iso = now.strftime("%Y-%m-%dT%H:%M:%S")
            exp_iso = (now + timedelta(hours=max(1, DOM_CACHE_TTL_HOURS))
                       ).strftime("%Y-%m-%dT%H:%M:%S")

            url_pattern = self._normalize_url(current_url)
            domain_key = self._extract_domain_key(current_url)
            task_intent = self._task_intent(user_task)
            step_text = self._step_context(step_context)
            dom_hash = self._compute_dom_hash(dom_skeleton)
            cache_id = f"{dom_hash}_{now.strftime('%Y%m%d%H%M%S')}"
            vectors = self._embed_fields(
                url_pattern=url_pattern,
                dom_skeleton=dom_skeleton,
                task_intent=task_intent,
                step_context=step_text,
            )

            payload = [
                [vectors["url_vector"]],
                [vectors["dom_vector"]],
                [vectors["task_vector"]],
                [vectors["step_vector"]],
                [cache_id],
                [url_pattern[:512]],
                [domain_key[:255]],
                [task_intent[:2000]],
                [step_text[:2000]],
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
        step_context: str = "",
    ) -> bool:
        if not locator_suggestions:
            logger.info("⏭️ [DomCache] Skip save: empty locator_suggestions")
            return False
        logger.info(
            f"📤 [DomCache] Submit async save, url={self._normalize_url(current_url)}, "
            f"task_len={len(user_task or '')}, step_len={len(step_context or '')}"
        )
        self._executor.submit(
            self._do_save_async,
            user_task,
            current_url,
            dom_skeleton,
            step_context,
            locator_suggestions,
        )
        return True


dom_cache_manager = DomCacheManager()
