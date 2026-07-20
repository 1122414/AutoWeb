from typing import Annotated, List, Optional, TypedDict, Dict, Any
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

# =============================================================================
# 自定义 Reducer：支持清空列表
# =============================================================================


def clearable_list_reducer(existing: List, update: Any) -> List:
    """
    可清空的列表 Reducer

    行为:
    - update 为 None → 返回空列表（清空）
    - update 为列表 → 追加到现有列表（与 operator.add 相同）
    - update 为其他 → 直接替换
    """
    if update is None:
        return []  # 清空
    # 字典协议处理：检测到带特定 flag 的字典，意味着要求强制替换
    if isinstance(update, dict) and "__replace__" in update:
        return update["__replace__"]
    if isinstance(update, list):
        return (existing or []) + update  # 追加
    return update  # 替换


class EnvState(TypedDict):
    """
    [环境感知状态]
    记录浏览器端和页面的客观状态
    """
    current_url: str
    dom_skeleton: str
    # 使用支持清空的 reducer
    locator_suggestions: Annotated[List[Dict[str,
                                             Any]], clearable_list_reducer]
    dom_hash: Optional[str]  # DOM MD5 哈希，用于检测页面变化 (Optimization)


class TaskState(TypedDict):
    """
    [任务进度状态]
    记录任务目标、计划和执行历史
    """
    user_task: str                      # 原始任务
    plan: Optional[str]                 # Planner 生成的最新计划

    # 使用支持清空的 reducer
    finished_steps: Annotated[List[str], clearable_list_reducer]

    # 使用支持清空的 reducer
    reflections: Annotated[List[str], clearable_list_reducer]

    is_complete: bool                   # 总体任务能否认为已完成
    loop_count: int                     # 防死循环步数控制


class AgentState(EnvState, TaskState):
    """
    [Core State v2]
    聚合所有子状态，作为 Graph 的主载体
    """
    # 基础消息历史 (LangGraph 内置 Reducer: add_messages)
    messages: Annotated[List[BaseMessage], add_messages]
    _task_started_at: Optional[str]      # 当前任务启动时间，用于同任务缓存隔离

    # 协作流转产生的临时数据 (由于 Node 是纯函数，这些通常作为 Node 的返回值传递，
    # 但为了方便下游 Node 读取，也可以存在 State 中，但不建议大量依赖)
    generated_code: Optional[str]       # Coder 生成的最新代码
    generated_action: Optional[Dict[str, Any]]
    execution_mode: Optional[str]       # "python_code" | "dp_cli" | None
    dpcli_session: Optional[str]
    dpcli_result: Optional[Dict[str, Any]]
    dpcli_snapshot: Optional[Dict[str, Any]]
    dpcli_snapshot_view: Optional[Dict[str, Any]]
    dpcli_snapshot_ref: Optional[Dict[str, Any]]       # Snapshot file references
    dpcli_agent_view: Optional[Dict[str, Any]]          # Lossy planner view (Layer 1)
    dpcli_snapshot_index: Optional[Dict[str, Any]]      # Index summary (Layer 2)
    dpcli_snapshot_delta: Optional[Dict[str, Any]]      # Added/removed/changed/rebound refs
    dpcli_observer_diagnostics: Optional[Dict[str, Any]] # Observer diagnostics
    dpcli_target_result: Optional[Dict[str, Any]]       # TargetSelector output
    dpcli_structured_plan: Optional[Dict[str, Any]]     # Planner structured intent
    dpcli_task_contract: Optional[Dict[str, Any]]       # End-to-end user crawl constraints
    dpcli_task_progress: Optional[Dict[str, Any]]       # Deduplicated rows/pages/failed refs
    dpcli_request_id: Optional[str]                     # Stable per-step CLI idempotency key
    dpcli_detail_batch_ran: bool
    execution_log: Optional[str]        # Executor 运行代码后的日志/返回值

    # Verifier 验收结果（供 Human-in-the-Loop 覆盖）
    # {is_success, is_done, summary}
    verification_result: Optional[Dict[str, Any]]

    # dp_cli 执行证据（P0-3: Executor 前后状态对比）
    # {before_url, after_url, url_changed, before_dom_hash, after_dom_hash,
    #  dom_changed, page_title, page_identity, viewport_scroll, action_skill, result_ok}
    dpcli_execution_evidence: Optional[Dict[str, Any]]
    dpcli_action_kind: Optional[str]
    dpcli_verification_contract: Optional[Dict[str, Any]]

    # Verifier 确定性判定策略配置（P3: 后续接口预留）
    # {min_target_confidence, schema_coverage_threshold,
    #  allow_low_confidence_success, llm_required_for_ambiguous_page_action}
    verification_policy: Optional[Dict[str, Any]]

    # 错误处理
    error: Optional[str]

    # Executor 微循环控制
    coder_retry_count: int              # Coder 重试计数（语法错误时微循环，最多3次）
    error_type: Optional[str]           # 错误类型: "syntax" | "locator" | "security" | "security_max_retry" | "syntax_max_retry" | "critical" | None

    # 代码缓存控制
    _code_source: Optional[str]         # 代码来源: "cache" | "llm" | None
    _action_source: Optional[str]
    _action_cache_hit_id: Optional[str]
    _failed_action_cache_ids: List[str]
    _dpcli_action_disabled: bool
    _cache_failed_this_round: bool      # 本轮缓存代码是否已失败（用于防止死循环）
    _cache_hit_id: Optional[str]        # 缓存命中记录 ID（用于失败失效）
    _failed_code_cache_ids: List[str]   # 当前失败窗口内禁用的 CodeCache 命中 ID

    # DOM 缓存控制
    _observer_source: Optional[str]     # 观察来源: "dom_cache" | "observer" | None
    _dom_cache_hit_id: Optional[str]    # DomCache 命中记录 ID（用于失败失效）
    _failed_dom_cache_ids: List[str]    # 当前失败窗口内禁用的 DomCache 命中 ID

    # 连续失败保底
    _step_fail_count: int               # 连续步骤失败计数（成功时重置为 0）
    _error_recovery_count: int          # 同一严重错误的连续恢复次数
    _last_recovery_error: Optional[str] # 最近一次严重错误指纹

    # RAG Node 控制
    # RAG 任务类型: "store_kb" | "store_cache" | "qa" | None
    rag_task_type: Optional[str]

    # Human-in-the-Loop 模式: "off" | "review_all"
    hitl_mode: Optional[str]
    human_approval_required: Optional[bool]
