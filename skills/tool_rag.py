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
                from langchain_milvus import Milvus
                from rag.retriever_qa import get_embedding_model
                from config import MILVUS_URI, KNOWLEDGE_COLLECTION_NAME

                self._embeddings = get_embedding_model()
                self._vector_store = Milvus(
                    embedding_function=self._embeddings,
                    connection_args={"uri": MILVUS_URI},
                    collection_name=KNOWLEDGE_COLLECTION_NAME,
                    consistency_level="Bounded",
                    auto_id=True,
                )
                print("   ✅ 连接建立成功")
            except Exception as e:
                print(f"   ❌ 连接失败: {e}")
                raise

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

        try:
            # 统一转换为文本列表
            texts = []
            if isinstance(content, str):
                texts = [content]
            elif isinstance(content, dict):
                # 字典转为 JSON 字符串或拼接值
                import json
                texts = [json.dumps(content, ensure_ascii=False, indent=2)]
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, str):
                        texts.append(item)
                    elif isinstance(item, dict):
                        import json
                        texts.append(json.dumps(
                            item, ensure_ascii=False, indent=2))

            # 过滤空内容和过长内容
            docs = []
            for text in texts:
                if len(text) < 10:
                    continue
                if len(text) > self.MAX_CONTENT_LENGTH:
                    text = text[:self.MAX_CONTENT_LENGTH] + "...[截断]"
                docs.append(Document(
                    page_content=text,
                    metadata={"source": source, "type": "crawled"}
                ))

            if not docs:
                return False

            with self.lock:
                self.buffer.extend(docs)
                buffer_size = len(self.buffer)

            print(f"📥 [KB] 已加入缓冲 ({buffer_size} 条待写入)")

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
            self._ensure_connection()
            self._vector_store.add_documents(docs)
            print(f"   ✅ [KB] 成功写入 {len(docs)} 条数据")
            return True
        except Exception as e:
            print(f"   ❌ [KB] 批量写入失败: {e}")
            return False

    def flush_and_wait(self, timeout: float = 30.0) -> bool:
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
        question (str): 用户的自然语言问题（完整问题，内部处理分析）。

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
