# ==============================================================================
# VectorCacheBase - 向量缓存管理器基类
# ==============================================================================
# 提取自 CodeCacheManager 和 DomCacheManager 的公共逻辑:
#   连接管理, Schema 校验, Embedding 获取, URL 标准化,
#   DOM 哈希, 缓存失效, 线程池管理
# ==============================================================================
import atexit
import hashlib
import re
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from pymilvus import (
    AnnSearchRequest,
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    WeightedRanker,
    utility,
)
from urllib.parse import urlparse

from config import MILVUS_URI
from skills.vector_gateway import (
    connect_milvus,
    hybrid_search,
    insert_and_flush,
    normalize_weights,
    read_hit_field,
)


class VectorCacheBase(ABC):
    """向量缓存管理器的抽象基类，封装与 Milvus 交互的通用逻辑。"""

    def __init__(self, weights: Tuple, defaults: Tuple, tag: str):
        self._collection: Optional[Collection] = None
        self._embeddings = None
        self._vector_dim: Optional[int] = None
        self._tag = tag
        self._weights = normalize_weights(weights, defaults=defaults, tag=tag)
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=tag
        )
        atexit.register(self._shutdown)

    # ------------------------------------------------------------------
    # 抽象方法 —— 子类必须实现
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def _collection_name(self) -> str:
        """Milvus Collection 名称"""

    @property
    @abstractmethod
    def _collection_description(self) -> str:
        """Milvus Collection 描述"""

    @abstractmethod
    def _schema_fields(self, dim: int) -> List[FieldSchema]:
        """返回 Schema 字段列表"""

    @abstractmethod
    def _vector_field_names(self) -> List[str]:
        """返回所有向量字段名列表，用于 Schema 校验和索引创建"""

    # ------------------------------------------------------------------
    # Embedding 管理
    # ------------------------------------------------------------------

    def _get_embeddings(self):
        if self._embeddings is None:
            from skills.vector_gateway import get_shared_embeddings
            self._embeddings = get_shared_embeddings()
        return self._embeddings

    def _get_vector_dim(self) -> int:
        if self._vector_dim is None:
            vec = self._get_embeddings().embed_query(f"{self._tag}_dim_probe")
            self._vector_dim = len(vec)
        return self._vector_dim

    # ------------------------------------------------------------------
    # Collection 管理
    # ------------------------------------------------------------------

    def _is_schema_compatible(self, collection: Collection, dim: int) -> bool:
        required = {f.name for f in self._schema_fields(dim) if f.name != "pk"}
        fields = {f.name: f for f in collection.schema.fields}
        if not required.issubset(fields.keys()):
            return False
        for name in self._vector_field_names():
            field = fields.get(name)
            if field is None or field.dtype != DataType.FLOAT_VECTOR:
                return False
            if int(field.params.get("dim", -1)) != dim:
                return False
        return True

    def _create_collection(self, dim: int) -> Collection:
        schema = CollectionSchema(
            fields=self._schema_fields(dim),
            description=self._collection_description,
            enable_dynamic_field=True,
        )
        collection = Collection(
            name=self._collection_name,
            schema=schema,
            consistency_level="Bounded",
        )

        vec_idx = {"metric_type": "COSINE",
                   "index_type": "AUTOINDEX", "params": {}}
        for field_name in self._vector_field_names():
            collection.create_index(
                field_name=field_name, index_params=vec_idx)

        # 标量倒排索引 (通用字段)
        for f in self._schema_fields(dim):
            if f.name in ("url_pattern", "dom_hash", "cache_id") and f.dtype == DataType.VARCHAR:
                try:
                    collection.create_index(
                        field_name=f.name, index_params={"index_type": "INVERTED"})
                except Exception:
                    pass

        collection.load()
        print(
            f"✅ [{self._tag}] Created collection '{self._collection_name}' (dim={dim})")
        return collection

    def _ensure_collection(self) -> Collection:
        if self._collection is not None:
            return self._collection
        connect_milvus(MILVUS_URI, alias="default", tag=self._tag)
        dim = self._get_vector_dim()
        name = self._collection_name

        if utility.has_collection(name):
            current = Collection(name)
            if not self._is_schema_compatible(current, dim):
                print(
                    f"⚠️ [{self._tag}] Incompatible schema in '{name}', dropping and recreating.")
                utility.drop_collection(name)
                current = self._create_collection(dim)
            else:
                current.load()
                print(f"📦 [{self._tag}] Reusing collection '{name}'")
            self._collection = current
            return self._collection

        self._collection = self._create_collection(dim)
        return self._collection

    # ------------------------------------------------------------------
    # 通用工具方法
    # ------------------------------------------------------------------

    def _normalize_url(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            domain = parsed.netloc
            if domain.lower().startswith("www."):
                domain = domain[4:]
            path = re.sub(r"/\d+", "/*", parsed.path or "")
            return f"{domain}{path}"[:512]
        except Exception:
            return (url or "")[:512]

    def _compute_dom_hash(self, dom_skeleton: str, max_len: int = 2500) -> str:
        content = (dom_skeleton or "")[:max_len]
        return hashlib.md5(content.encode("utf-8")).hexdigest()[:16]

    def _to_similarity(self, score: float) -> float:
        """将 Milvus 返回的距离/得分统一转为 [0, 1] 相似度"""
        value = float(score)
        if 0.0 <= value <= 1.0:
            return value
        if 1.0 < value <= 2.0:
            return max(0.0, 1.0 - value / 2.0)
        if -1.0 <= value < 0.0:
            return max(0.0, min(1.0, 1.0 + value))
        return max(0.0, min(1.0, 1.0 / (1.0 + abs(value))))

    def record_failure(self, cache_id: str, reason: str = "") -> None:
        """记录缓存命中失败（不删除缓存，仅做持久化标记供用户审查）

        失败可能是上下文相关的（DOM 临时变化、页面异常等），
        不代表缓存本身是坏数据。当前轮次的跳过由 _cache_failed_this_round
        熔断器保证，此方法只负责持久化记录。

        失败日志写入 output/cache_failures.jsonl，用户可审查后
        手动调用 invalidate() 删除确认无效的缓存。
        """
        if not cache_id:
            return
        import json as _json
        import os
        from datetime import datetime

        log_path = os.path.join("output", "cache_failures.jsonl")
        os.makedirs("output", exist_ok=True)

        entry = {
            "cache_id": cache_id,
            "cache_type": self._tag,
            "timestamp": datetime.now().isoformat(),
            "reason": reason,
        }
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
            print(f"📋 [{self._tag}] 已记录失败: {cache_id} (原因: {reason})")
            print(f"   ℹ️ 如需删除此缓存，请手动调用 invalidate('{cache_id}')")
        except Exception as e:
            print(f"⚠️ [{self._tag}] 记录失败日志异常: {e}")

    def invalidate(self, cache_id: str) -> bool:
        """手动删除指定缓存（仅供用户主动清理时调用）"""
        if not cache_id:
            return False
        try:
            collection = self._ensure_collection()
            safe = cache_id.replace('"', '\\"')
            collection.delete(expr=f'cache_id == "{safe}"')
            print(f"🗑️ [{self._tag}] Invalidated: {cache_id}")
            return True
        except Exception as exc:
            print(f"⚠️ [{self._tag}] Invalidate error: {exc}")
            return False

    def _shutdown(self):
        print(f"📧 [{self._tag}] Waiting for background tasks...")
        self._executor.shutdown(wait=True)
        print(f"✅ [{self._tag}] Background tasks finished")
