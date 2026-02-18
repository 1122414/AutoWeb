"""
AutoWeb 知识库管理器
====================
功能：
- 单例模式管理 Milvus 连接和 Embedding 模型
- 缓冲队列 + 批量异步写入
- 程序退出时同步刷新
"""
import sys
import os
import atexit
from typing import List, Dict, Union, Optional
from concurrent.futures import ThreadPoolExecutor, Future
from threading import Lock


# 确保项目根目录在 path 中
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class KnowledgeBaseManager:
    """
    知识库管理器（单例）

    使用方式:
        from skills.tool_rag import kb_manager
        kb_manager.add("爬取的文本内容", source="https://example.com")
        kb_manager.flush_and_wait()  # 程序退出前调用
    """
    _instance: Optional['KnowledgeBaseManager'] = None
    _initialized: bool = False

    # 配置
    BUFFER_THRESHOLD = 10  # 缓冲区阈值，达到后自动刷新
    MAX_CONTENT_LENGTH = 5000  # 单条内容最大长度

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if KnowledgeBaseManager._initialized:
            return
        KnowledgeBaseManager._initialized = True

        self.buffer: List = []  # 待写入的文档缓冲
        self.lock = Lock()  # 线程安全锁
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="kb_writer")
        self.pending_futures: List[Future] = []  # 跟踪异步任务

        # 延迟初始化（首次使用时才连接）
        self._embeddings = None
        self._vector_store = None

        # 注册程序退出时的清理函数
        atexit.register(self._cleanup)

        print("📚 [KnowledgeBaseManager] 初始化完成（延迟加载模式）")

    def _ensure_connection(self):
        """确保连接已建立（延迟初始化）"""
        if self._embeddings is None:
            print("🔌 [KnowledgeBaseManager] 建立 Embedding 和 Milvus 连接...")
            try:
                from config import MILVUS_URI
                from rag.retriever_qa import get_embedding_model
                from rag.milvus_schema import get_vector_store
                from skills.vector_gateway import connect_milvus

                connect_milvus(MILVUS_URI, alias="default",
                               tag="KnowledgeBaseManager")
                self._embeddings = get_embedding_model()
                self._vector_store = get_vector_store(self._embeddings)
                print("   ✅ 连接建立成功（Schema 已验证）")
            except Exception as e:
                print(f"   ❌ 连接失败: {e}")
                raise

    # 高频字段名列表（与 milvus_schema.py 中的固定字段保持一致）
    HIGH_FREQ_FIELDS = ["source", "title", "category",
                        "data_type", "platform", "crawled_at"]

    @staticmethod
    def _convert_dynamic_value(value):
        """
        对动态字段值做智能类型转换

        Returns:
            (converted_value, was_percent: bool)
            - "41.30"  → (41.30,  False)
            - "80.0%"  → (80.0,   True)   ← 标记为百分号来源
            - "¥4.32"  → (4.32,   False)
            - "-"      → (None,   False)
            - 纯文本   → ("text", False)
        """
        if not isinstance(value, str):
            return value, False  # int / float / bool 直接返回

        stripped = value.strip()

        # 无效值 → 不存入
        if stripped in ("", "-", "--", "N/A", "n/a", "null", "None"):
            return None, False

        # 去掉货币符号
        cleaned = stripped
        for prefix in ("¥", "$", "€", "£", "￥"):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
                break

        # 检测并去掉百分号
        was_percent = False
        if cleaned.endswith("%"):
            cleaned = cleaned[:-1].strip()
            was_percent = True

        # 去掉千分位逗号: "1,234.56" → "1234.56"
        cleaned = cleaned.replace(",", "")

        # 尝试转为数字
        try:
            return float(cleaned), was_percent
        except (ValueError, TypeError):
            pass

        return stripped, False  # 纯文本保持字符串

    def _extract_metadata(self, item: Dict, source: str) -> Dict:
        """
        从字典数据中提取 metadata

        高频字段放入对应 key，其他字段也放入 metadata（动态字段），
        自动注入 crawled_at 时间戳。
        动态字段值做智能类型转换（字符串数字→float），无效值不存入。
        """
        from datetime import datetime
        metadata = {}

        # 注入高频字段（有则取值，无则留空让 Schema 默认值处理）
        metadata["source"] = item.get("source", source)
        metadata["title"] = item.get("title", item.get("name", ""))
        metadata["category"] = item.get("category", item.get("type", ""))
        metadata["data_type"] = item.get("data_type", "crawled")
        metadata["platform"] = item.get("platform", "")
        metadata["crawled_at"] = item.get(
            "crawled_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        # 其他字段也放入 metadata（利用 Milvus 动态字段）
        pct_fields = []  # 记录原始值带 % 的字段名
        for key, value in item.items():
            if key not in self.HIGH_FREQ_FIELDS and key not in ("text", "content", "page_content"):
                # 只存标量值，跳过嵌套结构
                if isinstance(value, (str, int, float, bool)):
                    converted, was_pct = self._convert_dynamic_value(value)
                    if converted is not None:  # 跳过无效值
                        metadata[key] = converted
                        if was_pct:
                            pct_fields.append(key)

        # 存储百分号标记（批次检测时用，写入 Milvus 前会清理）
        if pct_fields:
            metadata["_pct_fields"] = pct_fields

        return metadata

    @staticmethod
    def _sanitize_format_consistency(docs: list, min_samples: int = 3):
        """
        批次内格式一致性检查

        针对场景：同一字段中大部分值是纯 float，少数值原始带 %
        规则：如果带 % 的值在该字段中是少数派 (< 50%)，则视为格式异常并移除
        """
        # 1. 统计每个数值字段的 pct / non-pct 分布
        # {field: {"total": N, "pct_count": M, "pct_docs": [(doc_idx, val), ...]}}
        field_stats = {}
        for i, doc in enumerate(docs):
            pct_fields = set(doc.metadata.get("_pct_fields", []))
            for k, v in doc.metadata.items():
                if k.startswith("_") or not isinstance(v, (int, float)):
                    continue
                if k not in field_stats:
                    field_stats[k] = {"total": 0,
                                      "pct_count": 0, "pct_docs": []}
                field_stats[k]["total"] += 1
                if k in pct_fields:
                    field_stats[k]["pct_count"] += 1
                    field_stats[k]["pct_docs"].append((i, v))

        # 2. 对 % 少数派字段，移除其异常值
        removed = 0
        for field, stats in field_stats.items():
            if stats["total"] < min_samples or stats["pct_count"] == 0:
                continue
            # 带 % 的是少数派 → 格式不一致 → 移除
            if stats["pct_count"] / stats["total"] < 0.5:
                for doc_idx, val in stats["pct_docs"]:
                    if field in docs[doc_idx].metadata:
                        del docs[doc_idx].metadata[field]
                        removed += 1
                        print(
                            f"⚠️ [KB] 格式不一致: {field}={val}"
                            f" (原始带 %, 与同字段其他纯数值不一致)，已移除"
                        )

        # 3. 清理内部标记 _pct_fields（不写入 Milvus）
        for doc in docs:
            doc.metadata.pop("_pct_fields", None)

        if removed:
            print(f"🧹 [KB] 本批次共清理 {removed} 个格式异常值")

    def _get_text_content(self, item) -> str:
        """
        从数据中提取 page_content 文本

        优先级：text > content > page_content > JSON 序列化
        """
        import json
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            # 优先取专用文本字段
            for key in ("text", "content", "page_content", "description", "summary"):
                if key in item and item[key]:
                    return str(item[key])
            # 没有专用字段，序列化整个 dict
            return json.dumps(item, ensure_ascii=False, indent=2)
        return str(item)

    def add(self, content: Union[str, Dict, List], source: str = "auto_crawl") -> bool:
        """
        添加内容到缓冲区（非阻塞）

        Args:
            content: 文本内容、字典或字典列表
            source: 数据来源标识

        Returns:
            bool: 是否成功加入缓冲
        """
        from langchain_core.documents import Document
        from rag.field_registry import register_fields
        from datetime import datetime

        try:
            # 统一转换为列表
            items = []
            if isinstance(content, str):
                items = [content]
            elif isinstance(content, dict):
                items = [content]
            elif isinstance(content, list):
                items = content

            docs = []
            all_field_samples = {}  # {field_name: sample_value} 用于推断类型

            for item in items:
                # 提取文本（内容）
                text = self._get_text_content(item)
                if len(text) < 10:
                    continue
                if len(text) > self.MAX_CONTENT_LENGTH:
                    text = text[:self.MAX_CONTENT_LENGTH] + "...[截断]"

                # 构建 metadata
                if isinstance(item, dict):
                    metadata = self._extract_metadata(item, source)
                    # 收集字段样本值（用于推断类型）
                    for k, v in metadata.items():
                        if k not in all_field_samples:
                            all_field_samples[k] = v
                else:
                    metadata = {
                        "source": source,
                        "title": "",
                        "category": "",
                        "data_type": "crawled",
                        "platform": "",
                        "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }

                docs.append(Document(page_content=text, metadata=metadata))

            if not docs:
                return False

            # 批次内格式一致性检查 (% vs 纯数值)
            self._sanitize_format_consistency(docs)

            # 注册字段到注册表（含类型信息）
            if all_field_samples:
                register_fields(all_field_samples)

            with self.lock:
                self.buffer.extend(docs)
                buffer_size = len(self.buffer)

            print(
                f"📥 [KB] 已加入缓冲 ({buffer_size} 条待写入, 字段: {len(all_field_samples)} 个)")

            # 达到阈值自动刷新
            if buffer_size >= self.BUFFER_THRESHOLD:
                self.flush_async()

            return True

        except Exception as e:
            print(f"❌ [KB] 添加失败: {e}")
            return False

    def flush_async(self) -> Optional[Future]:
        """
        异步刷新缓冲区（非阻塞）

        Returns:
            Future: 异步任务句柄，可用于等待完成
        """
        with self.lock:
            if not self.buffer:
                return None
            docs_to_save = self.buffer.copy()
            self.buffer.clear()

        print(f"🚀 [KB] 异步写入 {len(docs_to_save)} 条数据...")
        future = self.executor.submit(self._save_batch, docs_to_save)
        self.pending_futures.append(future)

        # 清理已完成的 Future
        self.pending_futures = [
            f for f in self.pending_futures if not f.done()]

        return future

    def _save_batch(self, docs: List) -> bool:
        """批量写入（在线程池中执行）"""
        try:
            from skills.vector_gateway import add_documents
            self._ensure_connection()
            add_documents(self._vector_store, docs, tag="KnowledgeBaseManager")
            # 显式 flush，确保数据持久化到 Milvus
            if self._vector_store and hasattr(self._vector_store, 'col') and self._vector_store.col:
                self._vector_store.col.flush()
                print(f"   ✅ [KB] 成功写入并 flush {len(docs)} 条数据")
            else:
                print(f"   ✅ [KB] 成功写入 {len(docs)} 条数据（无法 flush）")
            return True
        except Exception as e:
            print(f"   ❌ [KB] 批量写入失败: {e}")
            return False

    def flush_and_wait(self, timeout: float = 120.0) -> bool:
        """
        同步刷新并等待所有异步任务完成（程序退出时调用）

        Args:
            timeout: 最大等待时间（秒）

        Returns:
            bool: 是否全部完成
        """
        print("⏳ [KB] 正在刷新缓冲区并等待所有写入完成...")

        # 先刷新当前缓冲
        self.flush_async()

        # 等待所有任务完成
        from concurrent.futures import wait, FIRST_EXCEPTION

        if self.pending_futures:
            done, not_done = wait(self.pending_futures, timeout=timeout)

            if not_done:
                print(f"   ⚠️ [KB] {len(not_done)} 个任务超时未完成")
                return False

            # 检查是否有异常
            for future in done:
                try:
                    future.result()
                except Exception as e:
                    print(f"   ❌ [KB] 任务异常: {e}")

        print("   ✅ [KB] 所有写入任务已完成")
        return True

    def _cleanup(self):
        """程序退出时的清理（atexit 回调）"""
        print("\n🔄 [KB] 程序退出，正在清理...")
        self.flush_and_wait(timeout=10.0)
        self.executor.shutdown(wait=False)


# ==================== 全局单例 ====================
kb_manager = KnowledgeBaseManager()


# ==================== 便捷函数（向后兼容）====================

def ask_knowledge_base(question: str) -> str:
    """
    [RAG] 查询本地知识库。

    Args:
        question(str): 用户的自然语言问题（完整问题，内部处理分析）。

    Returns:
        str: 知识库的回答。
    """
    print(f"📚 [RAG] 正在查询知识库: {question}")

    try:
        from rag.retriever_qa import qa_interaction
        answer = qa_interaction(question)
        return answer
    except ImportError as e:
        return f"Error: RAG 模块未找到或导入失败。{e}"
    except Exception as e:
        return f"Error: 查询知识库时出错: {e}"


def save_to_knowledge_base(content: str, source: str = "auto_web_spider") -> bool:
    """
    [RAG] 将内容保存到知识库（异步非阻塞）

    Args:
        content: 文本内容
        source: 数据来源

    Returns:
        bool: 是否成功加入缓冲
    """
    return kb_manager.add(content, source)
