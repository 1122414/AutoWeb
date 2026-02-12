from functools import partial
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from core.state_v2 import AgentState
from core.nodes import (
    observer_node, planner_node, coder_node, executor_node,
    verifier_node, error_handler_node, cache_lookup_node, rag_node
)


def build_graph(checkpointer=None, llm=None, observer=None,
                coder_llm=None, planner_llm=None, verifier_llm=None):
    """
    构建 AutoWeb V5 Graph (Code Cache Edition)

    Args:
        checkpointer: LangGraph 检查点器
        llm: 默认 ChatOpenAI 实例（ErrorHandler 等节点的 LLM）
        observer: BrowserObserver 实例，用于环境感知
        coder_llm: Coder 节点专用 LLM（不传则使用 llm）
        planner_llm: Planner 节点专用 LLM（不传则使用 llm）
        verifier_llm: Verifier 节点专用 LLM（不传则使用 llm）

    由于 Nodes 现在直接返回 Command(goto="NextNode")，
    图的定义变得非常简洁，只需要添加节点和边即可。
    LangGraph 会自动遵守 Command 中的 goto 指令。

    V4 流程：
    START -> Observer -> Planner -> CacheLookup -> (Coder | Executor)
    Coder -> Executor -> Verifier -> (Observer | Planner)
    Planner 是唯一的 __end__ 出口
    """
    if llm is None:
        raise ValueError("LLM instance is required for build_graph")
    if observer is None:
        raise ValueError("Observer instance is required for build_graph")

    workflow = StateGraph(AgentState)

    # 1. Add Nodes - 使用 functools.partial 预绑定依赖
    # 各节点可使用独立 LLM，不传则回退到默认 llm
    workflow.add_node("Observer", partial(observer_node, observer=observer))
    workflow.add_node("CacheLookup", cache_lookup_node)  # [代码缓存检索]
    workflow.add_node("Planner", partial(planner_node, llm=planner_llm or llm))
    workflow.add_node("Coder", partial(coder_node, llm=coder_llm or llm))
    workflow.add_node("Executor", executor_node)  # Executor 不需要 LLM
    workflow.add_node("Verifier", partial(
        verifier_node, llm=verifier_llm or llm))
    workflow.add_node("RAGNode", rag_node)  # [V5] RAG 向量库操作节点
    workflow.add_node("ErrorHandler", partial(error_handler_node, llm=llm))

    # 2. Set Entry Point (从 Observer 开始)
    workflow.add_edge(START, "Observer")

    # 3. Dynamic Routing via Command Pattern
    # 在 LangGraph 0.2+ 中，节点返回 Command(goto="NodeName") 会自动处理跳转
    # 无需显式声明 add_conditional_edges

    # 4. Compile
    if checkpointer is None:
        checkpointer = MemorySaver()

    # [Human-in-the-Loop] 在 Executor 和 Verifier 执行前中断，等待用户确认
    app = workflow.compile(
        checkpointer=checkpointer,
        interrupt_before=["Executor"],
        interrupt_after=["Verifier"]  # Verifier 执行后中断，允许人工覆盖验收结果
    )
    return app
