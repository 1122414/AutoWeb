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

    # Planner -> Coder or END
    # workflow.add_conditional_edges(
    #     "Planner",
    #     route_after_planner,
    #     {
    #         "Coder": "Coder",
    #         END: END
    #     }
    # )
    
    # # Coder -> Executor (Linear)
    # workflow.add_edge("Coder", "Executor")
    
    # # Executor -> Verifier (Linear)
    # workflow.add_edge("Executor", "Verifier")
    
    # # Verifier -> Planner or END
    # workflow.add_conditional_edges(
    #     "Verifier",
    #     route_after_verifier,
    #     {
    #         "Planner": "Planner",
    #         END: END
    #     }
    # )
    
    # 3. Add Edges (Declaration Only)
    # 使用 Command 模式时，我们不需要显式的 add_conditional_edges 逻辑函数
    # 但我们应该声明潜在的边以供可视化（虽然 LangGraph 运行时不需要）
    # 在 0.2+ 中，只要 Node 返回 Command(goto=...)，运行时会自动处理跳转
    
    # 我们保留显式的 edge 添加主要为了逻辑清晰，
    # 但实际上只有那些非动态跳转（如果有的话）才必须在这里写。
    # 既然全部 dynamic，这里甚至不需要 add_edge，
    # 但为了图的连通性检查（如果有的话），我们还是可以加上。
    # 不过 LangGraph Command 允许跳转到任意节点。
    
    
    # 4. Compile
    if checkpointer is None:
        checkpointer = MemorySaver()
        
    # [Human-in-the-Loop] 在 Executor 执行前中断，等待用户确认或修改 Plan/Code
    app = workflow.compile(
        checkpointer=checkpointer,
        interrupt_before=["Executor"]
    )
    return app
