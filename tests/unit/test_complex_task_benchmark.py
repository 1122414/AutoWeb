from __future__ import annotations

from scripts.benchmark.benchmark_complex_tasks import CASES
from scripts.benchmark.benchmark_natural_language_agent import _evaluate
from scripts.benchmark.generate_complex_tasks_report import build_report


def test_complex_registry_covers_all_requested_capability_classes():
    assert set(CASES) == {
        "products_three_pages",
        "quotes_infinite_scroll",
        "books_list_detail",
        "hockey_filter_two_pages",
        "products_restart_resume",
    }
    capabilities = " ".join(case.capability for case in CASES.values())
    for expected in ("多页翻页", "无限滚动", "列表进入详情", "筛选后翻页", "中断恢复"):
        assert expected in capabilities
    assert CASES["products_restart_resume"].restart_after_pages == 1


def test_benchmark_evaluator_merges_later_detail_fields_by_url():
    case = CASES["books_list_detail"]
    url = "https://books.toscrape.com/catalogue/book_1/index.html"
    evaluation = _evaluate(
        case,
        status="completed",
        results=[
            {
                "ok": True,
                "action": "open",
                "data": {"page": {"url": case.url}},
            },
            {
                "ok": True,
                "action": "extract",
                "data": {
                    "items": [
                        {"title": f"Book {index}", "url": url.replace("1", str(index))}
                        for index in range(1, 6)
                    ]
                },
            },
            {
                "ok": True,
                "action": "batch-detail-extract",
                "data": {
                    "items": [
                        {
                            "title": f"Book {index}",
                            "url": url.replace("1", str(index)),
                            "final_url": url.replace("1", str(index)),
                            "detail_info": {"description": f"Description {index}"},
                        }
                        for index in range(1, 6)
                    ]
                },
            },
        ],
    )

    assert evaluation["checks"]["required_field_coverage_80pct"]
    assert evaluation["unique_item_count"] == 5


def test_complex_report_contains_original_tasks_and_restart_evidence():
    runs = []
    for index, case in enumerate(CASES.values(), start=1):
        runs.append(
            {
                "case": case.__dict__,
                "status": "completed",
                "elapsed_seconds": float(index),
                "event_count": 3,
                "events": [
                    {
                        "node": "Coder",
                        "generated_action": {"skill": "extract"},
                    }
                ],
                "restart_count": 1 if case.restart_after_pages else 0,
                "restart_checkpoint": (
                    {"completed_pages": [1], "item_count": 5, "active_page": 1}
                    if case.restart_after_pages
                    else None
                ),
                "source_file": f"C:/evidence/{case.key}.json",
                "evaluation": {
                    "accuracy_score": 100.0,
                    "unique_item_count": case.expected_min_items,
                    "checks": {"autonomous_completion": True},
                    "field_group_coverage": {"title|name": 1.0},
                    "item_sample": [{"title": "sample"}],
                },
            }
        )

    report = build_report(runs, generated_at="2026-07-21 00:00:00")

    assert "五类复杂任务全部通过真实公开站验证" in report
    for case in CASES.values():
        assert case.task in report
    assert "模拟中断" not in report
    assert "恢复证据" in report
    assert "completed_pages" not in report
