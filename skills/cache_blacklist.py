from __future__ import annotations

import time
from typing import Iterable, List, Set

from config import (
    CACHE_SOFT_BLACKLIST_BACKEND,
    CACHE_SOFT_BLACKLIST_ENABLED,
    CACHE_SOFT_BLACKLIST_REDIS_URL,
    CACHE_SOFT_BLACKLIST_TTL_SECONDS,
)
from skills.logger import logger


class CacheSoftBlacklist:
    """缓存软删除黑名单。

    设计目标：
    - 避免高频 Milvus delete 带来的 compaction 开销
    - 将失效缓存临时屏蔽，保留人工审查与手动 invalidate 能力
    """

    def __init__(self):
        self._enabled = CACHE_SOFT_BLACKLIST_ENABLED
        self._backend = CACHE_SOFT_BLACKLIST_BACKEND
        self._redis_url = CACHE_SOFT_BLACKLIST_REDIS_URL
        self._ttl = max(60, int(CACHE_SOFT_BLACKLIST_TTL_SECONDS or 0))
        self._redis = None
        self._local_store = {}

    def _now(self) -> int:
        return int(time.time())

    def _get_redis(self):
        if self._redis is not None:
            return self._redis
        import redis

        self._redis = redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def _redis_key(self, cache_type: str, domain_key: str) -> str:
        c = (cache_type or "unknown").strip().lower()
        d = (domain_key or "").strip().lower() or "unknown"
        return f"autoweb:cache_blacklist:{c}:{d}"

    def _cleanup_local(self, key: str) -> None:
        now = self._now()
        mapping = self._local_store.get(key, {})
        expired = [cache_id for cache_id, exp in mapping.items() if exp <= now]
        for cache_id in expired:
            mapping.pop(cache_id, None)
        if not mapping:
            self._local_store.pop(key, None)

    def mark_failed(
        self,
        *,
        cache_type: str,
        domain_key: str,
        cache_id: str,
        reason: str = "",
    ) -> bool:
        if (not self._enabled) or (not cache_id):
            return False

        key = self._redis_key(cache_type, domain_key)
        expire_at = self._now() + self._ttl

        if self._backend == "redis":
            try:
                r = self._get_redis()
                r.zadd(key, {cache_id: float(expire_at)})
                r.expire(key, self._ttl)
                logger.info(
                    f"⛔ [CacheBlacklist] 标记软删除 cache_id={cache_id}, "
                    f"domain={domain_key}, ttl={self._ttl}s"
                )
                return True
            except Exception as exc:
                logger.warning(f"⚠️ [CacheBlacklist] Redis 写入失败，降级本地内存: {exc}")

        bucket = self._local_store.setdefault(key, {})
        bucket[cache_id] = expire_at
        logger.info(
            f"⛔ [CacheBlacklist] 标记软删除(本地) cache_id={cache_id}, "
            f"domain={domain_key}, ttl={self._ttl}s"
        )
        return True

    def filter_allowed_ids(
        self,
        *,
        cache_type: str,
        domain_key: str,
        cache_ids: Iterable[str],
    ) -> List[str]:
        ids = [x for x in (cache_ids or []) if x]
        if (not self._enabled) or (not ids):
            return ids

        key = self._redis_key(cache_type, domain_key)
        now = self._now()

        if self._backend == "redis":
            try:
                r = self._get_redis()
                r.zremrangebyscore(key, "-inf", now)
                scores = r.zmscore(key, ids)
                allowed = [cid for cid, score in zip(ids, scores) if score is None]
                blocked = len(ids) - len(allowed)
                if blocked > 0:
                    logger.info(
                        f"⏭️ [CacheBlacklist] 过滤软删除命中 {blocked} 条, domain={domain_key}"
                    )
                return allowed
            except Exception as exc:
                logger.warning(f"⚠️ [CacheBlacklist] Redis 读取失败，降级本地内存: {exc}")

        self._cleanup_local(key)
        bucket = self._local_store.get(key, {})
        allowed = [cid for cid in ids if cid not in bucket]
        blocked = len(ids) - len(allowed)
        if blocked > 0:
            logger.info(
                f"⏭️ [CacheBlacklist] 过滤软删除命中(本地) {blocked} 条, domain={domain_key}"
            )
        return allowed


cache_soft_blacklist = CacheSoftBlacklist()
