from typing import Literal, Dict, Any
from langchain_core.messages import AIMessage
from core.state import AgentState

def admin_routing_logic(state: AgentState) -> Dict[str, Any]:
    """
    [Admin Node Logic]
    分析当前对话状态，决定任务流转的下一站。
    返回: {"next_role": "Planner" | "Coder" | "Executor" | "Verifier" | "FINISH"}
    """
    messages = state["messages"]
    step = state.get("loop_count", 0)
    is_complete = state.get("is_complete", False)
    
    print(f"\n👮 [Admin] 正在调度 (Step {step})...")
    
    # 0. 初始状态 -> Planner
    if step == 0 or not messages:
        return {"next_role": "Planner"}

    # 1. 优先检查明确的 State 标记
    # 如果 Verifier 标记任务已完成 -> 结束
    if is_complete:
        print("   -> 任务目标已达成，流程结束。")
        return {"next_role": "FINISH"}

    last_message = messages[-1]
    
    # 2. 状态机流转 (基于上一条消息的来源或内容)
    
    # (Checking last message type implies the PREVIOUS node's output)
    
    # Case A: Planner 刚发言 -> 交给 Coder
    if isinstance(last_message, AIMessage) and "【计划已生成】" in last_message.content:
        print("   -> 计划已更新，转交 Coder 实现...")
        return {"next_role": "Coder"}
        
    # Case B: Coder 刚发言 -> 交给 Executor
    if isinstance(last_message, AIMessage) and "【代码生成】" in last_message.content:
        print("   -> 代码已就绪，转交 Executor 执行...")
        return {"next_role": "Executor"}
        
    # Case C: Executor 刚发言 -> 交给 Verifier
    if isinstance(last_message, AIMessage) and "【执行报告】" in last_message.content:
        print("   -> 执行完毕，转交 Verifier 查验...")
        return {"next_role": "Verifier"}
        
    # Case D: Verifier 刚发言
    # 如果代码走到这里，说明 is_complete 是 False (否则在上面 #1 就退出了)
    if isinstance(last_message, AIMessage) and ("Status:" in last_message.content or "TaskDone:" in last_message.content):
        print("   -> 当前步骤完成，但任务未终结。回退 Planner 进行下一步规划...")
        return {"next_role": "Planner"}
    
    # 兜底：如果状态不明，默认回到 Planner 重新审视
    print("   -> 状态不明，回退给 Planner...")
    return {"next_role": "Planner"}

def route_supervisor(state: AgentState) -> Literal["Planner", "Coder", "Executor", "Verifier", "FINISH"]:
    """
    [Edge Routing Function]
    LangGraph 用来决定边走向的纯函数
    """
    # 这里的 next_role 是由 admin_routing_logic 计算并写入 state 的 (如果它是一个节点的话)
    # 但注意：在你的 graph.py 中，admin_node 是直接返回 {"next_role": ...} 的 update。
    # 这里的参数 state 是已经 update 过的。
    
    return state.get("next_role", "Planner")