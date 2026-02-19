import os
import sys
import torch
import httpx
import traceback
from typing import List, Tuple, Dict, Any, Optional
from dotenv import load_dotenv

# LangChain 相关
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.embeddings import OllamaEmbeddings
from langchain_milvus import Milvus
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun

# --- 混合检索相关 ---
from langchain_community.retrievers import BM25Retriever

# Transformers 相关 (用于 Rerank)
from transformers import AutoTokenizer, AutoModelForCausalLM

# 项目内部模块
from config import *
from rag.query_analyzer import query_analyzer
from rag.milvus_schema import get_vector_store, FIXED_FILTERABLE_FIELDS
from rag.field_registry import get_all_filterable_fields
from prompts.rag_prompts import RAG_PROMPT

# ==============================================================================
# 0. 全局配置与设备检测
# ==============================================================================
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
RERANK_MAX_LENGTH = 2048

# ==============================================================================
# 1. QwenReranker (重排序模型封装)
# ==============================================================================


class QwenReranker:
    """
    使用 Qwen (或兼容架构) 模型进行文档重排序 (Reranking)。
    单例模式，避免重复加载模型。
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            print(f"🚀 [System] Initializing QwenReranker on {DEVICE}...")
            cls._instance = super(QwenReranker, cls).__new__(cls)
            cls._instance.model = None
            cls._instance.tokenizer = None
            try:
                cls._instance._init_model()
            except Exception as e:
                print(f"❌ [Reranker] Model load failed: {e}")
                print(
                    "   -> Tip: Ensure 'transformers' and 'torch' are installed and RERANK_MODEL_PATH is correct.")
        return cls._instance

    def _init_model(self):
        # 延迟加载，节省资源
        self.tokenizer = AutoTokenizer.from_pretrained(
            RERANK_MODEL_PATH,
            padding_side='left',
            trust_remote_code=True
        )

        model_kwargs = {"device_map": DEVICE, "trust_remote_code": True}
        if DEVICE == "cuda":
            # 显存优化
            model_kwargs["torch_dtype"] = torch.float16

        self.model = AutoModelForCausalLM.from_pretrained(
            RERANK_MODEL_PATH,
            **model_kwargs
        ).eval()

        # 针对 Qwen Instruct 模型的打分 Token ID (Yes/No)
        # 注意：不同模型的 token id 不同，这里适配 Qwen/Qwen2
        self.token_false_id = self.tokenizer.convert_tokens_to_ids("no")
        self.token_true_id = self.tokenizer.convert_tokens_to_ids("yes")

        # 构造 Instruct Prompt (参考 BGE-Reranker-V2-Gemma 或类似 Instruct Reranker)
        self.prefix = "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query. Answer 'yes' or 'no'.<|im_end|>\n<|im_start|>user\n"
        self.suffix = "<|im_end|>\n<|im_start|>assistant\n"

        self.prefix_tokens = self.tokenizer.encode(
            self.prefix, add_special_tokens=False)
        self.suffix_tokens = self.tokenizer.encode(
            self.suffix, add_special_tokens=False)

    def _format_input(self, query: str, doc_content: str) -> str:
        return f"Query: {query}\nDocument: {doc_content[:1000]}"  # 截断防止OOM

    @torch.no_grad()
    def rerank(self, query: str, docs: List[Document], top_k: int = 5) -> List[Document]:
        if not docs or not self.model:
            return docs[:top_k]

        # 构造 Batch Input
        pairs = []
        for doc in docs:
            text = self._format_input(query, doc.page_content)
            pairs.append(text)

        # Tokenize
        inputs = self.tokenizer(
            pairs,
            padding=True,
            truncation=True,
            return_tensors="pt",
            max_length=RERANK_MAX_LENGTH
        ).to(self.model.device)

        # Forward pass (只计算 logits，不生成)
        outputs = self.model(**inputs)
        logits = outputs.logits[:, -1, :]  # 取最后一个 token 的 logits

        # 计算 Yes 的概率
        # 这里演示取 yes token 的 log_softmax
        scores = logits[:, self.token_true_id].float().cpu().numpy()

        # 排序
        doc_score_pairs = list(zip(docs, scores))
        doc_score_pairs.sort(key=lambda x: x[1], reverse=True)

        print(
            f"📊 [Rerank] Top score: {doc_score_pairs[0][1]:.4f} | Low score: {doc_score_pairs[-1][1]:.4f}")

        return [doc for doc, _ in doc_score_pairs[:top_k]]

# ==============================================================================
# 2. 核心辅助函数
# ==============================================================================


# ==============================================================================
# 2. 核心辅助函数
# ==============================================================================

_cached_embedding_model = None


def get_embedding_model():
    """自动选择 OpenAI 或 Ollama Embeddings (单例模式)"""
    global _cached_embedding_model
    if _cached_embedding_model is not None:
        return _cached_embedding_model

    http_client = httpx.Client(trust_env=False, timeout=60.0)

    if EMBEDDING_TYPE == 'local_ollama':
        # 清洗 base_url
        base_url = OPENAI_OLLAMA_BASE_URL.replace(
            "/api/generate", "").replace("/v1", "").rstrip("/")
        instance = OllamaEmbeddings(
            base_url=base_url, model=OPENAI_OLLAMA_EMBEDDING_MODEL)

    elif EMBEDDING_TYPE == 'local_vllm':
        instance = OpenAIEmbeddings(
            model=VLLM_OPENAI_EMBEDDING_MODEL,
            openai_api_key=VLLM_OPENAI_EMBEDDING_API_KEY,
            openai_api_base=VLLM_OPENAI_EMBEDDING_BASE_URL,
            http_client=http_client,
            check_embedding_ctx_length=False
        )
    else:
        instance = OpenAIEmbeddings(
            model=EMBEDDING_MODEL,
            openai_api_key=OPENAI_API_KEY,
            openai_api_base=OPENAI_OLLAMA_BASE_URL
        )

    _cached_embedding_model = instance
    print(f"🔗 [RAG] Embedding model initialized ({EMBEDDING_TYPE})")
    return _cached_embedding_model


def format_docs(docs):
    """格式化文档列表为上下文字符串，包含 metadata 动态字段"""
    parts = []
    for i, doc in enumerate(docs):
        text = f"[片段 {i+1}] {doc.page_content}"
        # 附加有意义的 metadata
        meta_parts = []
        for k, v in doc.metadata.items():
            if v and k not in ("text", "pk", "vector") and str(v).strip():
                meta_parts.append(f"{k}: {v}")
        if meta_parts:
            text += f"\n  元数据: {', '.join(meta_parts)}"
        parts.append(text)
    return "\n\n".join(parts)


def _cn_num_to_int(cn: str) -> int:
    """中文数字转阿拉伯数字（支持 一~九十九）"""
    digit_map = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                 "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if cn == "十":
        return 10
    result = 0
    for ch in cn:
        if ch == "十":
            result = (result or 1) * 10
        elif ch in digit_map:
            result += digit_map[ch]
    return result if result else 0


def get_retrieval_k(question: str) -> int:
    """根据问题类型动态调整 Top-K"""
    import re
    # 1. 解析 "前N名/top N" — 阿拉伯数字
    top_n_match = re.search(r'(?:前|top)\s*(\d+)', question, re.IGNORECASE)
    if top_n_match:
        n = int(top_n_match.group(1))
        return max(n * 2, 15)

    # 2. 解析 "前十名/前二十" — 中文数字
    cn_match = re.search(r'前([一二两三四五六七八九十]+)', question)
    if cn_match:
        n = _cn_num_to_int(cn_match.group(1))
        if n > 0:
            return max(n * 2, 15)

    # 3. 全局性查询
    global_keywords = ["全部", "所有", "列表", "清单",
                       "总结", "分析", "all", "summary", "list"]
    if any(kw in question.lower() for kw in global_keywords):
        return 15
    return 10

# ==============================================================================
# 3. 混合检索构建器
# ==============================================================================


class SimpleEnsembleRetriever(BaseRetriever):
    """
    手动实现的混合检索器，用于替代 langchain.retrievers.EnsembleRetriever
    使用加权倒数排名 (RRF) 算法合并结果。
    """
    retrievers: List[BaseRetriever]
    weights: List[float]

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None
    ) -> List[Document]:

        # 1. 并行或串行执行所有检索器
        # (简单实现为串行，生产环境可用 asyncio.gather)
        doc_lists = []
        for i, retriever in enumerate(self.retrievers):
            try:
                docs = retriever.invoke(
                    query,
                    config={"callbacks": run_manager.get_child()
                            if run_manager else None}
                )
                doc_lists.append(docs)
            except Exception as e:
                print(f"⚠️ [SimpleEnsemble] Retriever {i} failed: {e}")
                doc_lists.append([])

        # 2. RRF (Reciprocal Rank Fusion) 融合算法
        # 核心思想：排名越靠前，分数越高 (1 / (rank + c))
        c = 60  # RRF 常数，通常设为 60
        scores = {}

        for docs, weight in zip(doc_lists, self.weights):
            for rank, doc in enumerate(docs):
                # 使用内容作为 Key 进行去重 (Milvus返回的ID可能不一致)
                # 更好的做法是用 hash(doc.page_content)，这里直接用内容字符串
                key = doc.page_content

                if key not in scores:
                    scores[key] = {"doc": doc, "score": 0.0}

                # 加权分数累加
                scores[key]["score"] += weight * (1 / (rank + c))

        # 3. 根据最终 RRF 分数排序
        sorted_results = sorted(
            scores.values(), key=lambda x: x["score"], reverse=True)

        # 4. 返回 Document 对象列表
        return [item["doc"] for item in sorted_results]


# 5. 自定义分词器 (优化 BM25 召回)
# ==============================================================================
def custom_tokenizer(text: str) -> List[str]:
    """
    混合分词器 (jieba + 正则)：
    - 中文部分：jieba 精确模式分词 (人工智能 → [人工, 智能])
    - 英文/数字部分：正则按非字母数字符号切分 (kimi-2.5 → [kimi, 2, 5])
    - 所有 token 转小写、去空
    """
    import re
    import jieba

    text = text.lower()
    tokens = []

    # 按中文 vs 非中文交替切分
    segments = re.findall(r'[\u4e00-\u9fa5]+|[^\u4e00-\u9fa5]+', text)

    for seg in segments:
        if re.match(r'[\u4e00-\u9fa5]', seg):
            # 中文段 → jieba 分词
            tokens.extend(jieba.lcut(seg))
        else:
            # 英文/数字段 → 正则按符号切分
            parts = re.split(r'[^a-zA-Z0-9]+', seg)
            tokens.extend(parts)

    return [t for t in tokens if t.strip()]


def build_hybrid_retriever(milvus_store: Milvus, k: int):
    """
    构建混合检索器：Milvus (Dense) + BM25 (Sparse)
    """
    # 1. 准备 Milvus 检索器 (Dense - 语义检索)
    milvus_retriever = milvus_store.as_retriever(
        search_type="mmr",  # 使用 MMR 增加多样性
        # 后续可以通过category等筛选做混合检索
        search_kwargs={
            "k": k,
            "fetch_k": k * 2,
            "lambda_mult": 0.6
        }
    )

    # 2. 准备 BM25 检索器 (Sparse - 关键词检索)
    print("⏳ [Hybrid] 构建临时 BM25 索引 (In-Memory)...")
    bm25_retriever = None

    try:
        # 3. 优化采样策略：优先拉取最新的数据 (pk desc)
        # BM25 构建使用全量数据 (pk >= 0)，因为 filter_expr 可能不准确
        output_fields = ["text"] + list(FIXED_FILTERABLE_FIELDS)

        try:
            print(f" 🛡️ [BM25] Query with pk >= 0")
            # 增加 limit 到 5000 以覆盖更多数据 (视内存情况调整)
            res = milvus_store.col.query(
                expr="pk >= 0",
                output_fields=output_fields,
                limit=3000,
                offset=0
            )
            print(f"   ✅ [BM25] query returned {len(res)} docs")
        except Exception as e:
            print(f"   ⚠️ [BM25] query failed: {e}")
            res = []

        if res:
            bm25_docs = []
            for r in res:
                # 重建 Document 对象（动态提取固定字段）
                meta = {f: r.get(f, "") for f in FIXED_FILTERABLE_FIELDS}
                # Milvus LangChain 默认把 content 存在 'text' 字段
                text_content = r.get("text") or r.get("page_content") or ""
                if text_content:
                    bm25_docs.append(
                        Document(page_content=text_content, metadata=meta))

            if bm25_docs:
                # 注入自定义分词器
                bm25_retriever = BM25Retriever.from_documents(
                    bm25_docs,
                    preprocess_func=custom_tokenizer
                )
                bm25_retriever.k = k  # 设置 BM25 的召回数量
                print(
                    f"   -> BM25 索引构建完成 (Docs: {len(bm25_docs)}) | Tokenizer: Regex")
            else:
                print("   -> Milvus 返回数据为空，跳过 BM25")
        else:
            print("   -> 无法从 Milvus 拉取数据，跳过 BM25")

    except Exception as e:
        print(f"⚠️ [Hybrid] BM25 构建失败 (降级为纯向量检索): {e}")

    # 3. 组合 (Custom Ensemble)
    if bm25_retriever:
        print("🔗 [Hybrid] 启用混合检索: Milvus(0.5) + BM25(0.5)")
        # 使用我们自定义的 SimpleEnsembleRetriever
        return SimpleEnsembleRetriever(
            retrievers=[milvus_retriever, bm25_retriever],
            weights=[0.5, 0.5]  # 权重可调，0.5/0.5 是比较均衡的起点
        )
    else:
        print("⚠️ [Hybrid] 仅使用 Milvus 向量检索")
        return milvus_retriever

# ==============================================================================
# 4. RAG 主流程
# ==============================================================================


def _generate_answer(question: str, docs: List[Document]) -> str:
    """通用生成函数"""
    llm = ChatOpenAI(
        model=MODEL_NAME,
        temperature=0.1,
        max_tokens=4096,  # 防止长表格回答被截断
        openai_api_key=OPENAI_API_KEY,
        openai_api_base=OPENAI_BASE_URL
    )

    if RAG_PROMPT:
        if isinstance(RAG_PROMPT, str):
            custom_rag_prompt = PromptTemplate.from_template(RAG_PROMPT)
        else:
            custom_rag_prompt = RAG_PROMPT
    else:
        # 默认 Prompt
        template = """基于以下上下文回答问题。如果你不知道答案，请直接说不知道。\n\n上下文：\n{context}\n\n问题：{question}"""
        custom_rag_prompt = PromptTemplate.from_template(template)

    formatted_context = format_docs(docs)

    print("📝 [Generate] Generating answer...")
    chain = (
        custom_rag_prompt
        | llm
        | StrOutputParser()
    )

    return chain.invoke({"context": formatted_context, "question": question})


def _handle_sort_query(question: str, analysis: Dict) -> str:
    """处理排序类查询 (直接查库 + 排序)"""
    sort_field = analysis['sort_field']
    sort_order = analysis['sort_order']
    print(f"📉 [Sort Path] Field: {sort_field} | Order: {sort_order}")

    embeddings = get_embedding_model()
    vector_store = get_vector_store(embeddings)

    try:
        # 1. 拉取数据 (Limit 500 for memory safety)
        # 获取所有相关字段以供展示
        output_fields = ["text"] + list(FIXED_FILTERABLE_FIELDS)
        if sort_field not in output_fields:
            output_fields.append(sort_field)

        print(f"   🔍 Querying Milvus for sort: pk >= 0 (limit=500)")
        res = vector_store.col.query(
            expr="pk >= 0",
            output_fields=output_fields,
            limit=500  # 限制排序数据量
        )

        if not res:
            return "❌ 知识库为空，无法进行排序。"

        # 2. Python 内存排序
        def get_sort_val(item):
            val = item.get(sort_field)
            if val is None:
                return -float('inf') if sort_order == 'desc' else float('inf')
            try:
                return float(val)
            except ValueError:
                return str(val)

        reverse = (sort_order.lower() == "desc")
        sorted_res = sorted(res, key=get_sort_val, reverse=reverse)

        # 3. 截取 Top-K 并转换为 Documents
        k = get_retrieval_k(question)
        top_res = sorted_res[:k]

        docs = []
        for r in top_res:
            meta = {f: r.get(f, "") for f in FIXED_FILTERABLE_FIELDS}
            meta[sort_field] = r.get(sort_field, "")  # 确保排序字段可见

            text = r.get("text") or r.get("page_content") or ""
            if text:
                docs.append(Document(page_content=text, metadata=meta))

        if not docs:
            return "❌ 未找到有效数据进行排序。"

        # 4. 生成回答
        return _generate_answer(question, docs)

    except Exception as e:
        traceback.print_exc()
        return f"排序查询处理失败: {str(e)}"


def _handle_semantic_query(question: str, analysis: Dict) -> str:
    """处理语义检索查询 (RAG 流程)"""
    search_query = analysis['search_query']
    print(f"🧠 [Semantic Path] Query: {search_query}")

    embeddings = get_embedding_model()
    vector_store = get_vector_store(embeddings)

    # 1. Recall (Hybrid)
    target_k = get_retrieval_k(question)
    recall_k = target_k * 3

    # 注意：不再传入 filter_expr
    hybrid_retriever = build_hybrid_retriever(vector_store, recall_k)

    print(f"🔍 [Retrieve] Fetching candidates...")
    initial_docs = hybrid_retriever.invoke(search_query)

    if not initial_docs:
        return "❌ 没有在知识库中找到相关信息。"

    # 2. Deduplicate
    unique_docs = []
    seen_content = set()
    for doc in initial_docs:
        fingerprint = doc.page_content[:100]
        if fingerprint not in seen_content:
            unique_docs.append(doc)
            seen_content.add(fingerprint)

    print(f"   -> Retrieved {len(unique_docs)} unique docs.")

    # 3. Rerank
    print(f"⚖️ [Rerank] 使用 QwenReranker 进行精排...")
    try:
        reranker = QwenReranker()
        final_docs = reranker.rerank(question, unique_docs, top_k=target_k)
    except Exception as e:
        print(f"⚠️ Rerank failed: {e}, using raw retrieval results.")
        final_docs = unique_docs[:target_k]

    # 4. Generate
    return _generate_answer(question, final_docs)


def qa_interaction(question: str) -> str:
    print(f"\n🤔 [RAG] Searching for: {question}")

    # A. 意图分析
    analysis = {
        "filter_expr": "",
        "search_query": question,
        "sort_field": "",
        "sort_order": ""
    }

    if query_analyzer:
        try:
            # 使用新的 analyze 方法
            analysis = query_analyzer.analyze(question)
        except Exception as e:
            print(f"⚠️ Query analysis failed: {e}")

    # B. 分发逻辑
    if analysis.get("sort_field"):
        return _handle_sort_query(question, analysis)
    else:
        return _handle_semantic_query(question, analysis)


if __name__ == "__main__":
    # 测试入口
    q = sys.argv[1] if len(sys.argv) > 1 else "测试：介绍一下系统里的电影"
    print(qa_interaction(q))
