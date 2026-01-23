from typing import TypedDict, List, Dict, Any, Optional, Annotated
import operator
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    """
    [Core State]
    多 Agent 协作系统的共享上下文
    """
    # 1. 基础消息历史 (LangGraph 自动管理追加)
    messages: Annotated[List[BaseMessage], add_messages]
    
    # 2. 环境快照 (Observer 更新)
    current_url: str
    dom_skeleton: str
    locator_suggestions: Optional[str] # Planner 分析过的定位建议 (JSON String)，供 Coder 复用 -- [Optimization]
    
    # 3. 协作流转数据
    user_task: str                  # 原始任务
    plan: Optional[str]             # Planner 生成的自然语言计划
    generated_code: Optional[str]   # Coder 生成的 Python 代码
    execution_log: Optional[str]    # Executor 运行代码后的日志/返回值
    
    # 4. 控制位
    next_role: str                  # Admin 决定的下一个执行角色 (Planner/Coder/Executor/Verifier)
    loop_count: int                 # 防死循环计数
    
    # 5. 反思与记忆 (Reflexion)
    reflections: Annotated[List[str], operator.add] # 存储失败经验
    
    # 6. 迭代状态追踪
    finished_steps: List[str]       # 已完成的步骤记录
    is_complete: bool               # 总体任务能否认为已完成