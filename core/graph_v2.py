from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from core.state_v2 import AgentState
from core.nodes import planner_node, coder_node, executor_node, verifier_node, error_handler_node

def build_graph(checkpointer=None):
    """
    构建 AutoWeb V2 Graph (Command Pattern Edition)
    由于 Nodes 现在直接返回 Command(goto="NextNode")，
    图的定义变得非常简洁，只需要添加节点和边即可。
    LangGraph 会自动遵守 Command 中的 goto 指令。
    """
    workflow = StateGraph(AgentState)
    
    # 1. Add Nodes
    workflow.add_node("Planner", planner_node)
    workflow.add_node("Coder", coder_node)
    workflow.add_node("Executor", executor_node)
    workflow.add_node("Verifier", verifier_node)
    workflow.add_node("ErrorHandler", error_handler_node)
    
    # 2. Set Entry Point
    workflow.add_edge(START, "Planner")

    # 3. Dynamic Routing via Command Pattern
    # 在 LangGraph 0.2+ 中，节点返回 Command(goto="NodeName") 会自动处理跳转
    # 无需显式声明 add_conditional_edges
    
    
    # 4. Compile
    if checkpointer is None:
        checkpointer = MemorySaver()
        
    # [Human-in-the-Loop] 在 Executor 执行前中断，等待用户确认或修改 Plan/Code
    app = workflow.compile(
        checkpointer=checkpointer,
        interrupt_before=["Executor"]
    )
    return app
