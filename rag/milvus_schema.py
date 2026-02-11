"""
Milvus Collection Schema 管理
==============================
功能：
- 显式定义 spider_knowledge_base 的 Schema（高频固定字段 + 动态字段）
- 创建标量索引加速 expr 过滤
- 提供统一的 collection 初始化入口
"""
from config import MILVUS_URI, KNOWLEDGE_COLLECTION_NAME
from pymilvus import (
    connections, utility, Collection,
    CollectionSchema, FieldSchema, DataType,
    MilvusException
)
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ==============================================================================
# 高频固定字段定义
# ==============================================================================
# 这些字段可以建标量索引，expr 过滤速度远快于动态字段
FIXED_FILTERABLE_FIELDS = ["source", "title",
                           "category", "data_type", "platform", "crawled_at"]

# 字段默认值（写入时如果缺失则填充，避免 Milvus 报错）
FIELD_DEFAULTS = {
    "source": "",
    "title": "",
    "category": "",
    "data_type": "",
    "platform": "",
    "crawled_at": "",
}


def _build_schema(dim: int) -> CollectionSchema:
    """
    构建 spider_knowledge_base 的 Schema

    Args:
        dim: embedding 向量维度（由 embedding 模型决定）
    """
    fields = [
        FieldSchema(name="pk", dtype=DataType.INT64,
                    is_primary=True, auto_id=True),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
        FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=8000),

        # 高频过滤字段
        FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="data_type", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="platform", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="crawled_at", dtype=DataType.VARCHAR, max_length=32),
    ]

    schema = CollectionSchema(
        fields,
        description="AutoWeb 知识库 - 爬虫数据存储",
        enable_dynamic_field=True  # 允许任意额外字段
    )
    return schema


def _create_scalar_indexes(collection: Collection):
    """为高频字段创建标量索引（INVERTED 倒排索引）"""
    index_fields = ["source", "title", "category",
                    "data_type", "platform", "crawled_at"]

    for field_name in index_fields:
        try:
            collection.create_index(
                field_name=field_name,
                index_params={"index_type": "INVERTED"}
            )
            print(f"   ✅ 标量索引已创建: {field_name}")
        except MilvusException as e:
            if "already" in str(e).lower() or "exist" in str(e).lower():
                pass  # 索引已存在，跳过
            else:
                print(f"   ⚠️ 标量索引创建失败 ({field_name}): {e}")


def _parse_milvus_uri(uri: str):
    """从 MILVUS_URI 中解析 host 和 port"""
    uri = uri.replace("http://", "").replace("https://", "")
    parts = uri.split(":")
    host = parts[0]
    port = int(parts[1]) if len(parts) > 1 else 19530
    return host, port


def ensure_collection(embeddings) -> Collection:
    """
    确保 collection 存在且 Schema 正确。

    如果 collection 不存在，则创建新的（含标量索引）。
    如果已存在，直接返回。

    Args:
        embeddings: LangChain Embeddings 实例（用于探测向量维度）

    Returns:
        pymilvus.Collection 实例
    """
    host, port = _parse_milvus_uri(MILVUS_URI)

    # 连接 Milvus
    try:
        connections.connect(alias="default", host=host, port=port)
    except MilvusException:
        pass  # 可能已连接

    if utility.has_collection(KNOWLEDGE_COLLECTION_NAME):
        print(f"📦 [Schema] Collection '{KNOWLEDGE_COLLECTION_NAME}' 已存在")
        collection = Collection(KNOWLEDGE_COLLECTION_NAME)
        return collection

    # 探测 embedding 维度
    print(f"🔍 [Schema] 探测 embedding 维度...")
    test_vec = embeddings.embed_query("test")
    dim = len(test_vec)
    print(f"   -> 维度: {dim}")

    # 创建 collection
    print(f"🚀 [Schema] 创建 Collection '{KNOWLEDGE_COLLECTION_NAME}'...")
    schema = _build_schema(dim)
    collection = Collection(
        name=KNOWLEDGE_COLLECTION_NAME,
        schema=schema,
        consistency_level="Bounded"
    )

    # 创建向量索引
    collection.create_index(
        field_name="vector",
        index_params={
            "metric_type": "COSINE",
            "index_type": "AUTOINDEX",
        }
    )
    print(f"   ✅ 向量索引已创建")

    # 创建标量索引
    _create_scalar_indexes(collection)

    print(f"   ✅ Collection 创建完成 (dim={dim}, dynamic_field=True)")
    return collection


def get_vector_store(embeddings):
    """
    获取已正确初始化的 LangChain Milvus 实例。

    会先调用 ensure_collection() 确保 Schema 存在，
    然后返回 LangChain 的 Milvus wrapper。
    """
    from langchain_milvus import Milvus

    # 确保 collection 存在
    ensure_collection(embeddings)

    # 返回 LangChain Milvus 实例
    vector_store = Milvus(
        embedding_function=embeddings,
        connection_args={"uri": MILVUS_URI},
        collection_name=KNOWLEDGE_COLLECTION_NAME,
        consistency_level="Bounded",
        auto_id=True,
    )
    return vector_store
