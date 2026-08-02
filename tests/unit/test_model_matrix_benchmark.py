from __future__ import annotations

import json

from scripts.benchmark.benchmark_model_matrix import _matrix_summary
from scripts.benchmark.generate_model_comparison_report import generate_report


def _run(model: str, passed: bool, *, tokens: int, elapsed: float) -> dict:
    return {
        "model": model,
        "status": "completed" if passed else "failed",
        "elapsed_seconds": elapsed,
        "case": {
            "key": "example",
            "name": "Example task",
            "task": "Extract two records",
            "capability": "fixture",
        },
        "evaluation": {
            "checks": {"target_opened": passed, "autonomous_completion": passed},
            "accuracy_score": 100 if passed else 0,
            "unique_item_count": 2 if passed else 0,
            "item_sample": [],
        },
        "usage": {
            "total_tokens": tokens,
            "input_tokens": tokens - 10,
            "output_tokens": 10,
            "llm_call_count": 2,
            "llm_duration_ms": 1250,
            "estimated_call_count": 0,
            "token_precision": "provider_reported",
        },
        "source_artifact": "example.json",
    }


def test_matrix_summary_aggregates_model_metrics():
    summary = _matrix_summary(
        [
            _run("fast", True, tokens=100, elapsed=2.0),
            _run("fast", False, tokens=300, elapsed=4.0),
            _run("steady", True, tokens=150, elapsed=3.0),
        ]
    )

    fast = next(item for item in summary if item["model"] == "fast")
    assert fast["success_rate"] == 50.0
    assert fast["average_tokens"] == 200.0
    assert fast["average_elapsed_seconds"] == 3.0
    assert fast["llm_duration_seconds"] == 2.5


def test_combined_report_contains_task_and_model_tables(tmp_path):
    runs = [
        _run("deepseek-v4-flash", True, tokens=100, elapsed=2.0),
        _run("kimi-k2.6", False, tokens=200, elapsed=3.0),
    ]
    payload = {
        "configuration": {
            "models": ["deepseek-v4-flash", "kimi-k2.6"],
            "cases": ["example"],
            "repeats": 1,
            "max_resumes": 3,
            "enable_thinking": False,
            "cache_isolation": "disabled",
        },
        "runs": runs,
        "summary": _matrix_summary(runs),
    }
    matrix = tmp_path / "matrix.json"
    matrix.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "report.html"

    assert generate_report(matrix, output) == output
    document = output.read_text(encoding="utf-8")
    assert "实战爬虫与模型对比报告" in document
    assert "deepseek-v4-flash" in document
    assert "kimi-k2.6" in document
    assert "provider_reported" in document
