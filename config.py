import os
from dotenv import load_dotenv

# 1. 在这里统一加载 .env，其他文件就不需要再写 load_dotenv() 了
load_dotenv()

# ==========================
# 基础配置
# ==========================
MILVUS_URI = os.getenv("MILVUS_URI", "http://localhost:19530")
KNOWLEDGE_COLLECTION_NAME = "spider_knowledge_base"
CODE_COLLECTION_NAME = "code_cache"

# ==========================
# 魔搭模型参数配置（不加Ollama的全是魔搭、线上API）
# ==========================
# EMBEDDING_MODEL = os.getenv("MODA_EMBEDDING_MODEL", "text-embedding-3-small")
# MODEL_NAME = os.getenv("MODA_MODEL_NAME", "gpt-4o-mini")
# OPENAI_API_KEY = os.getenv("MODA_OPENAI_API_KEY")
# OPENAI_BASE_URL = os.getenv("MODA_OPENAI_BASE_URL")

# ==========================
# NVIDIA
# ==========================
# MODEL_NAME = os.getenv('NVIDIA_MODEL_NAME')
# OPENAI_API_KEY = os.getenv('NVIDIA_API_KEY')
# OPENAI_BASE_URL = os.getenv('NVIDIA_BASE_URL')

# ==========================
# 百炼
# ==========================
MODEL_NAME = os.getenv('BAILIAN_MODEL_NAME')
OPENAI_API_KEY = os.getenv('BAILIAN_API_KEY')
OPENAI_BASE_URL = os.getenv('BAILIAN_BASE_URL')

# ==========================
# 火山方舟
# ==========================
# MODEL_NAME = os.getenv('FANGZHOU_MODEL_NAME')
# OPENAI_API_KEY = os.getenv('FANGZHOU_API_KEY')
# OPENAI_BASE_URL = os.getenv('FANGZHOU_BASE_URL')

# ==========================
# 本地Ollama
# ==========================
OPENAI_OLLAMA_EMBEDDING_MODEL = os.getenv(
    "OPENAI_OLLAMA_EMBEDDING_MODEL", OPENAI_BASE_URL)
OPENAI_OLLAMA_BASE_URL = os.getenv("OPENAI_OLLAMA_BASE_URL")

EMBEDDING_TYPE = os.getenv("EMBEDDING_TYPE", "api").lower()

if OPENAI_BASE_URL:
    # 统一清洗逻辑：去除 /api/generate, /v1, 尾部斜杠
    # (注：保留你原始内容，防止破坏现有逻辑)
    if OPENAI_OLLAMA_BASE_URL:
        base_url = OPENAI_OLLAMA_BASE_URL.replace("/v1", "").strip("/")

# ==========================
# 服务器Vllm
# ==========================
VLLM_OPENAI_EMBEDDING_MODEL = os.getenv("VLLM_OPENAI_EMBEDDING_MODEL")
VLLM_OPENAI_EMBEDDING_API_KEY = os.getenv("VLLM_OPENAI_EMBEDDING_API_KEY")
VLLM_OPENAI_EMBEDDING_BASE_URL = os.getenv("VLLM_OPENAI_EMBEDDING_BASE_URL")

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL")

# Rerank 配置
RERANK_TYPE = os.getenv("RERANK_TYPE", "api").lower()
RERANK_MODEL_PATH = os.getenv("RERANK_MODEL_PATH")

# ==============================================================================
# 浏览器自动化配置 (Browser Pilot / DrissionPage)
# ==============================================================================

# 是否开启无头模式 (True=不显示界面，False=显示界面)
HEADLESS_MODE = os.getenv("HEADLESS_MODE", "False").lower() == "true"

# 浏览器用户数据目录 (核心：用于保持登录状态、Cookies、LocalStorage)
# 建议在 .env 中设置绝对路径，或者保持默认相对路径
BROWSER_USER_DATA_DIR = os.getenv("BROWSER_USER_DATA_DIR", "./browser_data")

# 浏览器启动参数 (默认针对 Linux/Docker 环境优化，Windows 下也适用)
BROWSER_ARGS = [
    '--no-sandbox',
    '--disable-gpu',
    '--disable-infobars',
    '--lang=zh-CN',
    '--ignore-certificate-errors',
    # '--start-maximized' # 如果需要启动最大化可取消注释
]

# ==============================================================================
# 存储与输出路径
# ==============================================================================

# 运行结果输出目录 (用于存放截图、下载的文件、生成的报告)
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./output")

# 关系型数据库连接串 (PostgreSQL)
# 格式示例: postgresql://user:password@localhost:5432/dbname
# POSTGRES_CONNECTION_STRING = os.getenv("POSTGRES_CONNECTION_STRING")

# ==============================================================================
# 各节点独立模型配置（不设置则使用全局默认值）
# ==============================================================================

# Coder 节点（代码生成，可使用专用代码模型如 DeepSeek-Coder）
# 默认使用上面的
CODER_MODEL_NAME = os.getenv('CODER_MODEL_NAME') or MODEL_NAME
CODER_API_KEY = os.getenv('CODER_API_KEY') or OPENAI_API_KEY
CODER_BASE_URL = os.getenv('CODER_BASE_URL') or OPENAI_BASE_URL

# Observer 节点（DOM 分析 + 定位策略生成）
OBSERVER_MODEL_NAME = os.getenv('OBSERVER_MODEL_NAME') or MODEL_NAME
OBSERVER_API_KEY = os.getenv('OBSERVER_API_KEY') or OPENAI_API_KEY
OBSERVER_BASE_URL = os.getenv('OBSERVER_BASE_URL') or OPENAI_BASE_URL

# Planner 节点（任务规划）
PLANNER_MODEL_NAME = os.getenv('PLANNER_MODEL_NAME') or MODEL_NAME
PLANNER_API_KEY = os.getenv('PLANNER_API_KEY') or OPENAI_API_KEY
PLANNER_BASE_URL = os.getenv('PLANNER_BASE_URL') or OPENAI_BASE_URL

# Verifier 节点（验收判断）
VERIFIER_MODEL_NAME = os.getenv('VERIFIER_MODEL_NAME') or MODEL_NAME
VERIFIER_API_KEY = os.getenv('VERIFIER_API_KEY') or OPENAI_API_KEY
VERIFIER_BASE_URL = os.getenv('VERIFIER_BASE_URL') or OPENAI_BASE_URL

# ==============================================================================
# 代码缓存配置 (Code Cache System)
# ==============================================================================

# 是否启用代码缓存复用 (True=复用历史代码，False=始终重新生成)
CODE_CACHE_ENABLED = os.getenv("CODE_CACHE_ENABLED", "True").lower() == "true"

# 代码缓存相似度阈值 (0-1，越高越严格)
CODE_CACHE_THRESHOLD = float(os.getenv("CODE_CACHE_THRESHOLD", "0.90"))

# 代码缓存 Collection 名称 (与知识库分开)
CODE_CACHE_COLLECTION = os.getenv("CODE_CACHE_COLLECTION", "code_cache")

# Code Cache 多向量融合权重 (goal + locator + user_task + url)
CODE_CACHE_WEIGHT_GOAL = float(os.getenv("CODE_CACHE_WEIGHT_GOAL", "0.6"))
CODE_CACHE_WEIGHT_LOCATOR = float(
    os.getenv("CODE_CACHE_WEIGHT_LOCATOR", "0.2"))
CODE_CACHE_WEIGHT_USER_TASK = float(
    os.getenv("CODE_CACHE_WEIGHT_USER_TASK", "0.1"))
CODE_CACHE_WEIGHT_URL = float(os.getenv("CODE_CACHE_WEIGHT_URL", "0.1"))

# ==============================================================================
# DOM 缓存配置 (Milvus Hybrid Search)
# ==============================================================================
DOM_CACHE_ENABLED = os.getenv("DOM_CACHE_ENABLED", "True").lower() == "true"
DOM_CACHE_COLLECTION = os.getenv("DOM_CACHE_COLLECTION", "dom_cache")
DOM_CACHE_THRESHOLD = float(os.getenv("DOM_CACHE_THRESHOLD", "0.9"))
DOM_CACHE_TOP_K = int(os.getenv("DOM_CACHE_TOP_K", "3"))
DOM_CACHE_TTL_HOURS = int(os.getenv("DOM_CACHE_TTL_HOURS", f"{24 * 7}"))
DOM_CACHE_TASK_MIN_SIM = float(os.getenv("DOM_CACHE_TASK_MIN_SIM", "0.8"))
DOM_CACHE_REQUIRE_URL_MATCH = os.getenv(
    "DOM_CACHE_REQUIRE_URL_MATCH", "True").lower() == "true"

# DOM Cache 融合权重 (url + dom + task)
DOM_CACHE_WEIGHT_URL = float(os.getenv("DOM_CACHE_WEIGHT_URL", "0.2"))
DOM_CACHE_WEIGHT_DOM = float(os.getenv("DOM_CACHE_WEIGHT_DOM", "0.5"))
DOM_CACHE_WEIGHT_TASK = float(os.getenv("DOM_CACHE_WEIGHT_TASK", "0.3"))
