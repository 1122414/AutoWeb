import unittest
from unittest.mock import patch

import test_dpcli_executor_node  # noqa: F401 - installs lightweight dependency stubs
from core.nodes import (
    _compact_dpcli_snapshot,
    _observer_dpcli_snapshot,
    _render_dpcli_snapshot_text,
)


SNAPSHOT = {
    "ok": True,
    "session": "unit",
    "action": "snapshot",
    "data": {
        "page": {"url": "https://example.test", "title": "Example"},
        "page_identity": {"page_id": "page_1"},
        "index": {
            "interactable_elements": [{"ref": f"e{i}"} for i in range(35)],
            "data_regions": [{"ref": f"r{i}"} for i in range(8)],
            "surface_index": [{"ref": f"s{i}"} for i in range(45)],
            "stats": {"total_nodes": 100},
        },
    },
    "error": None,
}


class DPCLIObserverProjectionTests(unittest.TestCase):
    def test_compact_snapshot_limits_large_sections(self):
        view = _compact_dpcli_snapshot(SNAPSHOT)

        self.assertEqual(view["page"]["url"], "https://example.test")
        self.assertEqual(len(view["interactable_elements"]), 30)
        self.assertEqual(len(view["data_regions"]), 5)
        self.assertEqual(len(view["surface_index"]), 40)

    def test_render_snapshot_text_contains_source_marker(self):
        text = _render_dpcli_snapshot_text(_compact_dpcli_snapshot(SNAPSHOT))

        self.assertIn("dp_cli_snapshot", text)
        self.assertIn("https://example.test", text)

    def test_observer_dpcli_snapshot_success_goes_to_planner(self):
        with patch("config.DPCLI_OBSERVER_ENABLED", True), \
                patch("config.DPCLI_OBSERVER_FALLBACK_TO_DOM", True), \
                patch("skills.dpcli_executor.DPCLIExecutor") as executor_cls:
            executor_cls.return_value.snapshot.return_value = SNAPSHOT
            command = _observer_dpcli_snapshot({"dpcli_session": "unit"})

        self.assertEqual(command.goto, "Planner")
        self.assertEqual(command.update["_observer_source"], "dp_cli")
        self.assertEqual(command.update["current_url"], "https://example.test")
        self.assertIn("dom_skeleton", command.update)

    def test_observer_uses_dpcli_snapshot_after_dpcli_action(self):
        with patch("config.DPCLI_OBSERVER_ENABLED", False), \
                patch("config.DPCLI_OBSERVER_FALLBACK_TO_DOM", True), \
                patch("skills.dpcli_executor.DPCLIExecutor") as executor_cls:
            executor_cls.return_value.snapshot.return_value = SNAPSHOT
            command = _observer_dpcli_snapshot({
                "execution_mode": "dp_cli",
                "dpcli_session": "unit",
                "dpcli_result": {"ok": True, "action": "open"},
            })

        self.assertEqual(command.goto, "Planner")
        self.assertEqual(command.update["_observer_source"], "dp_cli")
        self.assertEqual(command.update["current_url"], "https://example.test")

    def test_observer_dpcli_snapshot_failure_falls_back(self):
        failed = {
            "ok": False,
            "session": "unit",
            "action": "snapshot",
            "data": None,
            "error": {"code": "browser_config_error", "message": "bad", "details": {}},
        }
        with patch("config.DPCLI_OBSERVER_ENABLED", True), \
                patch("config.DPCLI_OBSERVER_FALLBACK_TO_DOM", True), \
                patch("skills.dpcli_executor.DPCLIExecutor") as executor_cls:
            executor_cls.return_value.snapshot.return_value = failed
            command = _observer_dpcli_snapshot({})

        self.assertIsNone(command)


if __name__ == "__main__":
    unittest.main()
