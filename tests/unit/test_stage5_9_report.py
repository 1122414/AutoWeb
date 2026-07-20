from __future__ import annotations

import sqlite3

from scripts.benchmark.generate_stage5_9_report import (
    build_report,
    load_trace_summaries,
)


def test_trace_summary_chooses_latest_thread_per_case(tmp_path):
    path = tmp_path / "trace.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE autoweb_run_trace (
                id INTEGER PRIMARY KEY,
                thread_id TEXT,
                event_type TEXT,
                total_tokens INTEGER,
                estimated_tokens INTEGER
            )
            """
        )
        connection.executemany(
            "INSERT INTO autoweb_run_trace VALUES (?, ?, ?, ?, ?)",
            [
                (1, "benchmark-products_three_pages-old", "llm", 100, 1),
                (2, "benchmark-products_three_pages-new", "llm", 40, 0),
                (
                    3,
                    "benchmark-products_three_pages-new",
                    "browser_action",
                    0,
                    0,
                ),
            ],
        )

    traces, totals = load_trace_summaries(
        path,
        [
            {
                "case": {"key": "products_three_pages"},
                "thread_ids": ["benchmark-products_three_pages-new"],
            }
        ],
    )

    assert traces["products_three_pages"]["tokens"] == 40
    assert totals == {
        "llm_calls": 1,
        "tokens": 40,
        "estimated_calls": 0,
        "browser_actions": 1,
    }


def test_report_contains_raw_task_commits_and_reliability_evidence():
    complex_payload = {
        "runs": [
            {
                "status": "completed",
                "elapsed_seconds": 1.5,
                "case": {
                    "key": "products_three_pages",
                    "capability": "三页翻页",
                    "name": "public test",
                    "task": "打开公开站并抓取三页",
                },
                "evaluation": {
                    "accuracy_score": 100,
                    "unique_item_count": 15,
                },
            }
        ]
    }
    reliability = {
        "passed": 1,
        "total": 1,
        "cases": [
            {
                "name": "expired_cache",
                "expected": "expired",
                "actual": "expired",
            }
        ],
    }
    report = build_report(
        complex_payload,
        reliability,
        {"products_three_pages": {"tokens": 0, "browser_actions": 7}},
        {
            "llm_calls": 0,
            "tokens": 0,
            "estimated_calls": 0,
            "browser_actions": 7,
        },
        [{"hash": "abc1234", "subject": "阶段五：恢复"}],
        [{"hash": "def5678", "subject": "阶段七：重绑定"}],
        generated_at="2026-07-21",
        autoweb_tests="329 passed",
        cli_tests="85 passed",
        complex_source="complex.json",
        reliability_source="lab.json",
    )

    assert "打开公开站并抓取三页" in report
    assert "abc1234" in report
    assert "expired_cache" in report
    assert "任务走确定性策略，未调用 LLM" in report
