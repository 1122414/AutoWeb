from __future__ import annotations

import json
import subprocess
import sys
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from skills.task_run_store import TaskRunStore, build_verified_action_key


class _CounterState(TypedDict):
    count: int


def _counter_graph(store: TaskRunStore):
    graph = StateGraph(_CounterState)
    graph.add_node("increment", lambda state: {"count": state["count"] + 1})
    graph.add_edge(START, "increment")
    graph.add_edge("increment", END)
    return graph.compile(checkpointer=store.checkpointer)


def test_sqlite_checkpointer_survives_store_reopen(tmp_path):
    path = tmp_path / "runs.sqlite3"
    config = {"configurable": {"thread_id": "cold-restart-thread"}}

    first = TaskRunStore(path)
    app = _counter_graph(first)
    assert app.invoke({"count": 0}, config)["count"] == 1
    first.record_snapshot(
        "cold-restart-thread",
        {
            "user_task": "跨进程恢复",
            "current_url": "https://example.test/page/1",
            "dpcli_session": "cold-session",
            "dpcli_task_progress": {"items": [{"id": 1}], "completed_pages": [1]},
        },
    )
    first.close()

    second = TaskRunStore(path)
    restored = _counter_graph(second).get_state(config)
    manifest = second.get_manifest("cold-restart-thread")
    second.close()

    assert restored.values["count"] == 1
    assert manifest is not None
    assert manifest.dpcli_session == "cold-session"
    assert manifest.completed_pages == [1]
    assert manifest.item_count == 1


def test_manifest_status_and_verified_action_key_are_deterministic(tmp_path):
    values = {
        "_task_started_at": "2026-07-21T01:02:03",
        "user_task": "抓取两页",
        "current_url": "https://example.test/page/2",
        "dpcli_session": "session-a",
        "dpcli_request_id": "req-2",
        "dpcli_result": {"ok": True, "action": "extract"},
        "dpcli_task_progress": {
            "items": [{"url": "/1"}, {"url": "/2"}],
            "completed_pages": [1, 2],
        },
    }
    store = TaskRunStore(tmp_path / "runs.sqlite3")
    first = store.record_snapshot("thread-1", values, ("Verifier",))
    second = store.record_snapshot("thread-1", values, ("Verifier",))
    store.close()

    assert first.status == "interrupted"
    assert first.last_verified_action_key == second.last_verified_action_key
    assert first.last_verified_action_key == build_verified_action_key(values)
    assert second.created_at == first.created_at


def test_checkpoint_is_readable_from_a_new_python_process(tmp_path):
    path = tmp_path / "cross-process.sqlite3"
    script = """
import json
import sys
from typing import TypedDict
from langgraph.graph import END, START, StateGraph
from skills.task_run_store import TaskRunStore

class State(TypedDict):
    count: int

store = TaskRunStore(sys.argv[2])
graph = StateGraph(State)
graph.add_node("increment", lambda state: {"count": state["count"] + 1})
graph.add_edge(START, "increment")
graph.add_edge("increment", END)
app = graph.compile(checkpointer=store.checkpointer)
config = {"configurable": {"thread_id": "process-boundary"}}
if sys.argv[1] == "write":
    value = app.invoke({"count": 40}, config)["count"]
else:
    value = app.get_state(config).values["count"]
print(json.dumps({"count": value}))
store.close()
"""
    first = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script, "write", str(path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    second = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script, "read", str(path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert json.loads(first.stdout)["count"] == 41
    assert json.loads(second.stdout)["count"] == 41
