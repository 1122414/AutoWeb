import sys
import os

# 确保项目根目录在 path 中，以便能 import rag
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def ask_knowledge_base(question: str) -> str:
    """
    [RAG] 查询本地知识库。
    
    Args:
        question (str): 用户的自然语言问题。
        
    Returns:
        str: 知识库的回答。
    """
    print(f"📚 [Tool_RAG] Querying Knowledge Base: {question}")
    
    try:
        # Lazy Import: 只有在真正调用时才加载 RAG 模块 (因为它很重，加载 Torch/Milvus 需要时间)
        from rag.retriever_qa import qa_interaction
        
        answer = qa_interaction(question)
        return answer
    except ImportError as e:
        return f"Error: RAG module not found or failed to import. {e}"
    except Exception as e:
        return f"Error querying knowledge base: {e}"

def save_to_knowledge_base(content: str, source: str = "auto_web_spider") -> bool:
    """
    [RAG] 将爬取到的文本内容保存到本地知识库 (Milvus)。
    
    Args:
        content (str): 文本内容。
        source (str): 数据来源标识 (如 URL 或文件名)。
        
    Returns:
        bool: 是否保存成功。
    """
    if len(content) < 10: return False
    print(f"💾 [Tool_RAG] Saving to Knowledge Base (Size: {len(content)})...")
    
    try:
        # Lazy Import
        from langchain_milvus import Milvus
        from langchain_core.documents import Document
        from rag.retriever_qa import get_embedding_model
        from config import MILVUS_URI, KNOWLEDGE_COLLECTION_NAME
        
        embeddings = get_embedding_model()
        
        # Connect to Milvus
        vector_store = Milvus(
            embedding_function=embeddings,
            connection_args={"uri": MILVUS_URI},
            collection_name=KNOWLEDGE_COLLECTION_NAME,
            consistency_level="Bounded",
            auto_id=True,
        )
        
        # Wrap content as Document
        doc = Document(page_content=content, metadata={"source": source, "type": "crawled"})
        
        # Add to store
        vector_store.add_documents([doc])
        print("   ✅ Saved successfully.")
        return True
        
    except Exception as e:
        print(f"❌ [Tool_RAG] Save Error: {e}")
        return False
