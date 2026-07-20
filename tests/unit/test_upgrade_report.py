from __future__ import annotations

import unittest
from pathlib import Path

from scripts.benchmark.generate_upgrade_report import (
    _is_strict_live_pass,
    build_report,
)


def _successful_run(case_key: str) -> dict:
    return {
        "case": {"key": case_key, "name": case_key},
        "status": "completed",
        "evaluation": {
            "accuracy_score": 100.0,
            "unique_item_count": 5,
            "checks": {
                "field_coverage": True,
                "item_count": True,
                "known_anchor": True,
                "autonomous_finish": True,
            },
        },
        "session_close": {"ok": True},
        "exception": None,
    }


class TestUpgradeReport(unittest.TestCase):
    def test_strict_live_pass_requires_closed_session(self) -> None:
        run = _successful_run("books_static")
        run["session_close"]["ok"] = False

        self.assertFalse(_is_strict_live_pass(run))

    def test_complete_five_site_matrix_reports_threshold_proven(self) -> None:
        case_keys = (
            "books_static",
            "quotes_static",
            "quotes_js",
            "products_pagination",
            "hockey_table",
        )
        runs = [
            _successful_run(case_key)
            for case_key in case_keys
            for _ in range(3)
        ]
        report = build_report(
            live_matrix={"runs": runs},
            replay={
                "summary": {
                    "projection_replay_passes": 5,
                    "case_count": 5,
                    "full_tasks_proven_by_saved_pages": 4,
                },
                "cases": [],
            },
            audit={
                "summary": {
                    "component_passes": 5,
                    "case_count": 5,
                    "component_pass_rate": 100.0,
                },
                "cases": [],
            },
            live_matrix_path=Path("matrix.json"),
            replay_path=Path("replay.json"),
            audit_path=Path("audit.json"),
        )

        self.assertIn("最终“成功率超过 80%”已证明", report)
        self.assertIn("15/15", report)
        self.assertNotIn("仍未证明", report)


if __name__ == "__main__":
    unittest.main()
