from unittest.mock import MagicMock

import pytest
from langgraph.checkpoint.memory import MemorySaver

from core.graph_v2 import build_graph


def test_graph_requires_explicit_runtime_dependencies():
    with pytest.raises(ValueError, match="LLM"):
        build_graph(checkpointer=MemorySaver())
    with pytest.raises(ValueError, match="Observer"):
        build_graph(
            checkpointer=MemorySaver(),
            llm=MagicMock(),
        )


def test_graph_registers_command_nodes_and_hitl_interrupts():
    graph = build_graph(
        checkpointer=MemorySaver(),
        llm=MagicMock(),
        observer=MagicMock(),
    )

    assert graph.interrupt_before_nodes == ["Executor"]
    assert graph.interrupt_after_nodes == ["Verifier"]
    assert {
        "Observer",
        "Planner",
        "CacheLookup",
        "Coder",
        "Executor",
        "Verifier",
        "ErrorHandler",
    }.issubset(graph.get_graph().nodes)
