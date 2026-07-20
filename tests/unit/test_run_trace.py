from __future__ import annotations

from types import SimpleNamespace

from scripts.benchmark.generate_run_trace_report import build_report
from skills.run_trace import (
    RunTraceStore,
    TraceEvent,
    trace_browser_action,
    traced_llm_invoke,
)


class _FakeLLM:
    model_name = "priced-model"

    def __init__(self, response):
        self.response = response

    def invoke(self, _messages):
        return self.response


def test_real_usage_metadata_is_aggregated_with_configured_cost(tmp_path):
    store = RunTraceStore(
        tmp_path / "trace.sqlite3",
        pricing={
            "priced-model": {
                "input_per_million": 2.0,
                "output_per_million": 4.0,
            }
        },
    )
    response = SimpleNamespace(
        content="done",
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
        },
        response_metadata={},
    )

    result = traced_llm_invoke(
        _FakeLLM(response),
        ["prompt"],
        node="Planner",
        state={"user_task": "task"},
        config={"configurable": {"thread_id": "run-1"}},
        store=store,
    )
    trace_browser_action(
        config={"configurable": {"thread_id": "run-1"}},
        state={"current_url": "https://example.test"},
        action={"skill": "click", "request_id": "req-1"},
        result={"ok": True},
        duration_ms=25,
        store=store,
    )
    summary = store.summarize("run-1")

    assert result is response
    assert summary.total_tokens == 120
    assert summary.estimated_call_count == 0
    assert summary.browser_action_count == 1
    assert summary.cost_usd == 0.00028


def test_missing_usage_is_estimated_and_labeled(tmp_path):
    store = RunTraceStore(tmp_path / "trace.sqlite3")
    response = SimpleNamespace(
        content="estimated output",
        usage_metadata={},
        response_metadata={},
    )

    traced_llm_invoke(
        _FakeLLM(response),
        ["estimate this prompt"],
        node="Coder",
        config={"configurable": {"thread_id": "run-estimated"}},
        store=store,
    )
    summary = store.summary_dict("run-estimated")
    report = build_report(summary, store.events("run-estimated"))

    assert summary["total_tokens"] > 0
    assert summary["estimated_call_count"] == 1
    assert "（估算）" in report
    assert "run-estimated" in report


def test_trace_store_keeps_arbitrary_structured_evidence(tmp_path):
    store = RunTraceStore(tmp_path / "trace.sqlite3")
    store.append(
        TraceEvent(
            thread_id="run-evidence",
            event_type="verification",
            node="Verifier",
            model="deterministic",
            started_at="2026-07-21T00:00:00+00:00",
            duration_ms=1.5,
            payload={"decision_source": "task_contract", "ok": True},
        )
    )

    event = store.events("run-evidence")[0]
    assert event["payload"]["decision_source"] == "task_contract"
    assert store.summarize("run-evidence").event_count == 1

