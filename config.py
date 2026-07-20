import os
import json
from dotenv import load_dotenv

# 1. 在这里统一加载 .env
load_dotenv()


def _env_bool(name: str, default: str = "False") -> bool:
    raw = os.getenv(name, default)
    value = str(raw or "").strip().lower()
    return value in {"1", "true", "t", "yes", "y", "on"}


def _env_csv(name: str, default):
    raw = os.getenv(name, "")
    if not raw.strip():
        return default
    return [x.strip() for x in raw.split(",") if x.strip()]


def _env_float(name: str, default: str) -> float:
    return float(os.getenv(name, default))


def _env_rule_list(name: str, default):
    """
    Env JSON format:
    [{"label":"xxx","pattern":"regex"}, ...]
    """
    raw = os.getenv(name, "")
    if not raw.strip():
        return default
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return default
        rules = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", "")).strip()
            pattern = str(item.get("pattern", "")).strip()
            if label and pattern:
                rules.append((label, pattern))
        return rules or default
    except Exception:
        return default


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
# dp_cli adapter config
# ==============================================================================
DPCLI_ENABLED = _env_bool("DPCLI_ENABLED", "False")
DPCLI_CWD = os.getenv("DPCLI_CWD", r"E:\GitHub\Repositories\drissionpage-cli")
DPCLI_PYTHON = os.getenv("DPCLI_PYTHON", "python")
DPCLI_SESSION = os.getenv("DPCLI_SESSION", "autoweb")
DPCLI_HEADLESS = _env_bool("DPCLI_HEADLESS", "False")
DPCLI_TIMEOUT_SECONDS = float(os.getenv("DPCLI_TIMEOUT_SECONDS", "60"))
DPCLI_BATCH_TIMEOUT_SECONDS = float(
    os.getenv("DPCLI_BATCH_TIMEOUT_SECONDS", "900"))
DPCLI_OBSERVER_ENABLED = _env_bool("DPCLI_OBSERVER_ENABLED", "False")
DPCLI_OBSERVER_FALLBACK_TO_DOM = _env_bool(
    "DPCLI_OBSERVER_FALLBACK_TO_DOM", "True")
DPCLI_FULL_SNAPSHOT_MODE = _env_bool("DPCLI_FULL_SNAPSHOT_MODE", "True")
ACTION_CACHE_ENABLED = _env_bool("ACTION_CACHE_ENABLED", "False")
ACTION_CACHE_THRESHOLD = float(os.getenv("ACTION_CACHE_THRESHOLD", "0.75"))
ACTION_CACHE_STORE_PATH = os.getenv(
    "ACTION_CACHE_STORE_PATH", os.path.join(os.getenv("OUTPUT_DIR", "./output"), "action_cache.json"))

# ==============================================================================
# 存储与输出路径
# ==============================================================================

# 运行结果输出目录 (用于存放截图、下载的文件、生成的报告)
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./output")

# Task Run durable checkpoint storage. SQLite is the default local adapter.
TASK_RUN_PERSISTENCE_ENABLED = _env_bool(
    "TASK_RUN_PERSISTENCE_ENABLED", "True")
TASK_RUN_DB_PATH = os.getenv(
    "TASK_RUN_DB_PATH",
    os.path.join(OUTPUT_DIR, "state", "autoweb_task_runs.sqlite3"),
)

RUN_TRACE_ENABLED = _env_bool("RUN_TRACE_ENABLED", "True")
RUN_TRACE_DB_PATH = os.getenv(
    "RUN_TRACE_DB_PATH",
    os.path.join(OUTPUT_DIR, "traces", "autoweb_run_trace.sqlite3"),
)
try:
    LLM_PRICING = json.loads(os.getenv("LLM_PRICING_JSON", "{}"))
    if not isinstance(LLM_PRICING, dict):
        LLM_PRICING = {}
except json.JSONDecodeError:
    LLM_PRICING = {}

# Production governance: cache admission and ethical site access.
CACHE_GOVERNANCE_ENABLED = _env_bool("CACHE_GOVERNANCE_ENABLED", "True")
CACHE_GOVERNANCE_ALLOW_LEGACY_FINGERPRINT = _env_bool(
    "CACHE_GOVERNANCE_ALLOW_LEGACY_FINGERPRINT",
    "True",
)
ACTION_CACHE_TTL_HOURS = int(os.getenv("ACTION_CACHE_TTL_HOURS", "168"))
CODE_CACHE_TTL_HOURS = int(os.getenv("CODE_CACHE_TTL_HOURS", f"{24 * 30}"))

SITE_POLICY_ENABLED = _env_bool("SITE_POLICY_ENABLED", "True")
SITE_POLICY_ROBOTS_ENABLED = _env_bool("SITE_POLICY_ROBOTS_ENABLED", "True")
SITE_POLICY_ROBOTS_FAIL_OPEN = _env_bool(
    "SITE_POLICY_ROBOTS_FAIL_OPEN",
    "True",
)
SITE_POLICY_ALLOW_PRIVATE = _env_bool("SITE_POLICY_ALLOW_PRIVATE", "False")
SITE_POLICY_MIN_INTERVAL_SECONDS = _env_float(
    "SITE_POLICY_MIN_INTERVAL_SECONDS",
    "0.5",
)
SITE_POLICY_ROBOTS_TIMEOUT_SECONDS = _env_float(
    "SITE_POLICY_ROBOTS_TIMEOUT_SECONDS",
    "5",
)
SITE_POLICY_USER_AGENT = os.getenv(
    "SITE_POLICY_USER_AGENT",
    "AutoWeb/6 (+https://github.com/1122414/AutoWeb)",
)

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
# Verifier 确定性信号增强策略配置 (P0-P2)
# ==============================================================================

# TargetSelector 最低置信度阈值 (0-1)，低于此值不能确定成功
VERIFIER_MIN_TARGET_CONFIDENCE = _env_float("VERIFIER_MIN_TARGET_CONFIDENCE", "0.8")

# Data schema 字段覆盖率阈值 (0-1)，items 中的 schema 字段覆盖比例需达此值
VERIFIER_SCHEMA_COVERAGE_THRESHOLD = _env_float("VERIFIER_SCHEMA_COVERAGE_THRESHOLD", "0.6")

# 是否允许低置信度场景下的确定性成功（建议默认 False，交给 LLM）
VERIFIER_ALLOW_LOW_CONFIDENCE_SUCCESS = _env_bool("VERIFIER_ALLOW_LOW_CONFIDENCE_SUCCESS", "False")

# 模糊 page 动作是否强制要求 LLM 仲裁
VERIFIER_LLM_REQUIRED_FOR_AMBIGUOUS_PAGE = _env_bool("VERIFIER_LLM_REQUIRED_FOR_AMBIGUOUS_PAGE", "True")

# 重复动作检测相似度阈值 (0-1, difflib.SequenceMatcher)
VERIFIER_DUPLICATE_ACTION_THRESHOLD = _env_float("VERIFIER_DUPLICATE_ACTION_THRESHOLD", "0.92")

# 重复动作检测最小触发次数
VERIFIER_DUPLICATE_ACTION_MIN_COUNT = int(os.getenv("VERIFIER_DUPLICATE_ACTION_MIN_COUNT", "2"))

# 连续失败次数达到此值时升级 failure_scope 为 global
VERIFIER_FAIL_COUNT_GLOBAL_ESCALATE = int(os.getenv("VERIFIER_FAIL_COUNT_GLOBAL_ESCALATE", "2"))

# 连续失败次数达到此值时建议终止任务
VERIFIER_FAIL_COUNT_TERMINATE = int(os.getenv("VERIFIER_FAIL_COUNT_TERMINATE", "5"))

# ==============================================================================
# 代码缓存配置 (Code Cache System)
# ==============================================================================

# 是否启用代码缓存复用 (True=复用历史代码，False=始终重新生成)
CODE_CACHE_ENABLED = os.getenv("CODE_CACHE_ENABLED", "True").lower() == "true"

# 代码缓存相似度阈值 (0-1，越高越严格)
CODE_CACHE_THRESHOLD = float(os.getenv("CODE_CACHE_THRESHOLD", "0.95"))

# 代码缓存 Collection 名称 (与知识库分开)
CODE_CACHE_COLLECTION = os.getenv("CODE_CACHE_COLLECTION", "code_cache")

# Code Cache 多向量融合权重 (goal + locator + user_task + url)
CODE_CACHE_WEIGHT_GOAL = float(os.getenv("CODE_CACHE_WEIGHT_GOAL", "0.6"))
CODE_CACHE_WEIGHT_LOCATOR = float(
    os.getenv("CODE_CACHE_WEIGHT_LOCATOR", "0.2"))
CODE_CACHE_WEIGHT_USER_TASK = float(
    os.getenv("CODE_CACHE_WEIGHT_USER_TASK", "0.1"))
CODE_CACHE_WEIGHT_URL = float(os.getenv("CODE_CACHE_WEIGHT_URL", "0.1"))

# Code Cache 行为阈值
CODE_CACHE_SIMILARITY_THRESHOLD = float(
    os.getenv("CODE_CACHE_SIMILARITY_THRESHOLD", "0.0"))
CODE_CACHE_DUPLICATE_THRESHOLD = float(
    os.getenv("CODE_CACHE_DUPLICATE_THRESHOLD", "0.95"))
CODE_CACHE_NAV_MAX_LEN = int(os.getenv("CODE_CACHE_NAV_MAX_LEN", "200"))
CODE_CACHE_MAX_CODE_WARN = int(os.getenv("CODE_CACHE_MAX_CODE_WARN", "6400"))

# Code Cache 分阶段检索阈值
CODE_CACHE_STAGE2_TASK_MIN_SIM = float(
    os.getenv("CODE_CACHE_STAGE2_TASK_MIN_SIM", "0.80"))
CODE_CACHE_STAGE3_GOAL_MIN_SIM = float(
    os.getenv("CODE_CACHE_STAGE3_GOAL_MIN_SIM", "0.88"))
CODE_CACHE_CANDIDATE_TOP_K = int(
    os.getenv("CODE_CACHE_CANDIDATE_TOP_K", "30"))

# Code Cache Dry-Run 配置（避免 SPA/懒加载假阴性）
CODE_CACHE_DRY_RUN_ENABLED = _env_bool("CODE_CACHE_DRY_RUN_ENABLED", "True")
CODE_CACHE_DRY_RUN_TIMEOUT_SECONDS = float(
    os.getenv("CODE_CACHE_DRY_RUN_TIMEOUT_SECONDS", "5"))

# Observer (DomCache) Dry-Run 配置（Dom 命中后前置探测）
DOM_CACHE_DRY_RUN_ENABLED = _env_bool("DOM_CACHE_DRY_RUN_ENABLED", "True")
DOM_CACHE_DRY_RUN_TIMEOUT_SECONDS = float(
    os.getenv("DOM_CACHE_DRY_RUN_TIMEOUT_SECONDS", "5"))

# Observer(LLM) Dry-Run 配置（Observer 实时生成定位后立即探测）
OBSERVER_DRY_RUN_ENABLED = _env_bool("OBSERVER_DRY_RUN_ENABLED", "True")
OBSERVER_DRY_RUN_TIMEOUT_SECONDS = float(
    os.getenv("OBSERVER_DRY_RUN_TIMEOUT_SECONDS", "5"))
OBSERVER_DRY_RUN_MAX_RETRIES = int(
    os.getenv("OBSERVER_DRY_RUN_MAX_RETRIES", "2"))
OBSERVER_DRY_RUN_FAIL_RATIO_THRESHOLD = float(
    os.getenv("OBSERVER_DRY_RUN_FAIL_RATIO_THRESHOLD", "0.5"))

# ==============================================================================
# DOM 缓存配置 (Milvus Hybrid Search)
# ==============================================================================
DOM_CACHE_ENABLED = os.getenv("DOM_CACHE_ENABLED", "True").lower() == "true"
DOM_CACHE_COLLECTION = os.getenv("DOM_CACHE_COLLECTION", "dom_cache")
DOM_CACHE_THRESHOLD = float(os.getenv("DOM_CACHE_THRESHOLD", "0.95"))
DOM_CACHE_TOP_K = int(os.getenv("DOM_CACHE_TOP_K", "3"))
DOM_CACHE_TTL_HOURS = int(os.getenv("DOM_CACHE_TTL_HOURS", f"{24 * 7}"))
DOM_CACHE_TASK_MIN_SIM = float(os.getenv("DOM_CACHE_TASK_MIN_SIM", "0.8"))
DOM_CACHE_REQUIRE_URL_MATCH = os.getenv(
    "DOM_CACHE_REQUIRE_URL_MATCH", "True").lower() == "true"
DOM_CACHE_STEP_WINDOW = int(os.getenv("DOM_CACHE_STEP_WINDOW", "5"))
DOM_CACHE_STEP_TEXT_MAX = int(os.getenv("DOM_CACHE_STEP_TEXT_MAX", "1200"))

# DOM Cache 融合权重 (url + dom + task)
DOM_CACHE_WEIGHT_URL = float(os.getenv("DOM_CACHE_WEIGHT_URL", "0.2"))
DOM_CACHE_WEIGHT_DOM = float(os.getenv("DOM_CACHE_WEIGHT_DOM", "0.5"))
DOM_CACHE_WEIGHT_TASK = float(os.getenv("DOM_CACHE_WEIGHT_TASK", "0.3"))
DOM_CACHE_WEIGHT_STEP = float(os.getenv("DOM_CACHE_WEIGHT_STEP", "0.15"))

# DOM Cache 分阶段检索阈值
DOM_CACHE_STAGE2_TASK_MIN_SIM = float(
    os.getenv("DOM_CACHE_STAGE2_TASK_MIN_SIM", "0.80"))
DOM_CACHE_STAGE3_SCORE_THRESHOLD = float(
    os.getenv("DOM_CACHE_STAGE3_SCORE_THRESHOLD", "0.90"))
DOM_CACHE_STAGE3_WEIGHT_DOM = float(
    os.getenv("DOM_CACHE_STAGE3_WEIGHT_DOM", "0.65"))
DOM_CACHE_STAGE3_WEIGHT_STEP = float(
    os.getenv("DOM_CACHE_STAGE3_WEIGHT_STEP", "0.35"))
DOM_CACHE_CANDIDATE_TOP_K = int(
    os.getenv("DOM_CACHE_CANDIDATE_TOP_K", "32"))
DOM_CACHE_DUPLICATE_THRESHOLD = float(
    os.getenv("DOM_CACHE_DUPLICATE_THRESHOLD", "0.95"))

# 软删除黑名单（替代高频 Milvus Delete）
CACHE_SOFT_BLACKLIST_ENABLED = _env_bool(
    "CACHE_SOFT_BLACKLIST_ENABLED", "True")
CACHE_SOFT_BLACKLIST_BACKEND = os.getenv(
    "CACHE_SOFT_BLACKLIST_BACKEND", "redis").strip().lower()
CACHE_SOFT_BLACKLIST_REDIS_URL = os.getenv(
    "CACHE_SOFT_BLACKLIST_REDIS_URL",
    os.getenv("REDIS_URL", "redis://localhost:6379/0")
)
CACHE_SOFT_BLACKLIST_TTL_SECONDS = int(
    os.getenv("CACHE_SOFT_BLACKLIST_TTL_SECONDS", f"{24 * 3600}"))

# ==============================================================================
# Human-in-the-Loop (HITL) 配置
# ==============================================================================
HITL_MODE_DEFAULT = os.getenv("HITL_MODE_DEFAULT", "off").strip().lower()
HITL_FORCE_STEP_FAIL_THRESHOLD = int(
    os.getenv("HITL_FORCE_STEP_FAIL_THRESHOLD", "2"))

# Hard-gate toggles
HITL_FORCE_EXEC_HIGH_RISK = _env_bool("HITL_FORCE_EXEC_HIGH_RISK", "True")
HITL_FORCE_EXEC_IRREVERSIBLE = _env_bool(
    "HITL_FORCE_EXEC_IRREVERSIBLE", "True")
HITL_FORCE_VERIFIER_LOW_CONF = _env_bool(
    "HITL_FORCE_VERIFIER_LOW_CONF", "True")
HITL_FORCE_VERIFIER_LOG_CONFLICT = _env_bool(
    "HITL_FORCE_VERIFIER_LOG_CONFLICT", "True")

# Executor hard-gate rules (JSON override supported)
# Env: HITL_EXEC_HIGH_RISK_RULES_JSON='[{"label":"x","pattern":"..."}]'
HITL_EXEC_HIGH_RISK_RULES = _env_rule_list(
    "HITL_EXEC_HIGH_RISK_RULES_JSON",
    [
        ("file_or_dir_delete",
         r"\b(os\.(remove|rmdir|removedirs)|shutil\.rmtree)\s*\("),
        ("system_command_exec",
         r"\b(os\.system|subprocess\.(run|Popen|call|check_call|check_output))\s*\("),
        ("outbound_write_request", r"\brequests\.(post|put|delete|patch)\s*\("),
    ],
)

HITL_EXEC_IRREVERSIBLE_RULES = _env_rule_list(
    "HITL_EXEC_IRREVERSIBLE_RULES_JSON",
    [
        ("form_submit", r"\.submit\s*\("),
        ("irreversible_click",
         r"(click|js_click)\s*\([^)]*(delete|pay|submit|confirm|checkout|purchase)"),
    ],
)

# Verifier hard-gate rules
HITL_VERIFIER_LOW_CONF_REGEX = os.getenv(
    "HITL_VERIFIER_LOW_CONF_REGEX",
    r"(不确定|无法确认|可能|也许|疑似|maybe|uncertain|not sure|likely)"
)
HITL_VERIFIER_FATAL_KEYWORDS = _env_csv(
    "HITL_VERIFIER_FATAL_KEYWORDS",
    ["runtime error", "traceback", "elementnotfound", "timeoutexception",
        "execution failed", "critical"],
)
HITL_VERIFIER_SUCCESS_KEYWORDS = _env_csv(
    "HITL_VERIFIER_SUCCESS_KEYWORDS",
    ["success", "succeed", "completed", "done",
        "saved", "成功", "完成", "已保存", "执行完成"],
)

# ==============================================================================
# 路由关键词 (Routing Keywords)
# ==============================================================================
# RAG 路由: Planner 根据计划内容分派到 RAGNode
RAG_STORE_KEYWORDS = ["存入向量", "存入知识库", "save_to_kb", "向量数据库", "Milvus"]
RAG_QA_KEYWORDS = ["查询知识库", "根据知识库回答", "从知识库中", "知识库问答"]

# RAG 完成拦截: Planner 判定完成前检查是否还需要执行 RAG 存储
RAG_GOAL_KEYWORDS = ["向量数据库", "知识库", "Milvus", "save_to_kb", "存入向量"]
RAG_DONE_KEYWORDS = ["store_kb", "存入向量", "已存入知识库", "RAG存储"]

# 任务连续性检测: 包含这些词时视为延续任务
CONTINUE_KEYWORDS = ["继续", "接着", "下一页", "翻页", "再爬", "追加", "补充", "当前页面"]

# ==============================================================================
# 上下文裁剪配置 (Context Pruning - tiktoken 水位监控)
# ==============================================================================
PLANNER_CONTEXT_WINDOW = int(os.getenv('PLANNER_CONTEXT_WINDOW', '32000'))
CONTEXT_PRUNE_RATIO = float(os.getenv('CONTEXT_PRUNE_RATIO', '0.8'))
CONTEXT_RECENT_KEEP = int(os.getenv('CONTEXT_RECENT_KEEP', '3'))
CONTEXT_MAX_UNIQUE_PAGES = int(os.getenv('CONTEXT_MAX_UNIQUE_PAGES', '5'))
CONTEXT_MAX_REFLECTIONS = int(os.getenv('CONTEXT_MAX_REFLECTIONS', '3'))
CONTEXT_MAX_MESSAGE_ROUNDS = int(os.getenv('CONTEXT_MAX_MESSAGE_ROUNDS', '5'))

# 摘要压缩小模型（用于 finished_steps 滚动摘要，默认复用全局模型）
SUMMARIZER_MODEL_NAME = os.getenv('SUMMARIZER_MODEL_NAME') or MODEL_NAME
SUMMARIZER_API_KEY = os.getenv('SUMMARIZER_API_KEY') or OPENAI_API_KEY
SUMMARIZER_BASE_URL = os.getenv('SUMMARIZER_BASE_URL') or OPENAI_BASE_URL


def log_config_summary():
    """输出配置摘要到 sys_log（延迟导入避免循环依赖）"""
    from skills.logger import logger
    logger.info("\n" + "=" * 50)
    logger.info("[config] 配置加载完成")
    logger.info("=" * 50)
    logger.info(f"   MODEL: {MODEL_NAME}")
    logger.info(f"   BASE_URL: {OPENAI_BASE_URL}")
    logger.info(f"   EMBEDDING: {EMBEDDING_MODEL} (type={EMBEDDING_TYPE})")
    logger.info(f"   DPCLI_ENABLED: {DPCLI_ENABLED}")
    logger.info(f"   CODE_CACHE_ENABLED: {CODE_CACHE_ENABLED}")
    logger.info(f"   DOM_CACHE_ENABLED: {DOM_CACHE_ENABLED}")
    logger.info(f"   ACTION_CACHE_ENABLED: {ACTION_CACHE_ENABLED}")
    logger.info(f"   HITL_MODE_DEFAULT: {HITL_MODE_DEFAULT}")
    logger.info(f"   HEADLESS_MODE: {HEADLESS_MODE}")
    logger.info("=" * 50)
