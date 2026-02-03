import operator
from typing import Annotated, List, Optional, TypedDict, Union, Dict, Any
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class EnvState(TypedDict):
    """
    [环境感知状态]
    记录浏览器端和页面的客观状态
    """
    current_url: str
    dom_skeleton: str
    # 累积的定位策略列表，每个元素包含 {page_context, url, strategies}
    locator_suggestions: Annotated[List[Dict[str, Any]], operator.add]
    dom_hash: Optional[str]  # DOM MD5 哈希，用于检测页面变化 (Optimization)

class TaskState(TypedDict):
    """
    [任务进度状态]
    记录任务目标、计划和执行历史
    """
    user_task: str                      # 原始任务
    plan: Optional[str]                 # Planner 生成的最新计划
    
    # 使用 Reducer 防止覆盖，记录完整的步骤历史
    finished_steps: Annotated[List[str], operator.add] 
    
    # 存储失败经验 (Reflexion)
    reflections: Annotated[List[str], operator.add] 
    
    is_complete: bool                   # 总体任务能否认为已完成
    loop_count: int                     # 防死循环步数控制

class AgentState(EnvState, TaskState):
    """
    [Core State v2]
    聚合所有子状态，作为 Graph 的主载体
    """
    # 基础消息历史 (LangGraph 内置 Reducer: add_messages)
    messages: Annotated[List[BaseMessage], add_messages]
    
    # 协作流转产生的临时数据 (由于 Node 是纯函数，这些通常作为 Node 的返回值传递，
    # 但为了方便下游 Node 读取，也可以存在 State 中，但不建议大量依赖)
    generated_code: Optional[str]       # Coder 生成的最新代码
    execution_log: Optional[str]        # Executor 运行代码后的日志/返回值
    
    # Verifier 验收结果（供 Human-in-the-Loop 覆盖）
    verification_result: Optional[Dict[str, Any]]  # {is_success, is_done, summary}
    
    # 错误处理
    error: Optional[str]
    
    # [V3] Executor 微循环控制
    coder_retry_count: int              # Coder 重试计数（语法错误时微循环，最多3次）
    error_type: Optional[str]           # 错误类型: "syntax" | "locator" | None
    
    # [V4] 代码缓存控制
    _code_source: Optional[str]         # 代码来源: "cache" | "llm" | None
    _cache_failed_this_round: bool      # 本轮缓存代码是否已失败（用于防止死循环）

