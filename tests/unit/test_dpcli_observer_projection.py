import unittest
from unittest.mock import patch

import tests.unit.stubs  # noqa: F401 - installs lightweight dependency stubs
from core.nodes import (
    _compact_dpcli_snapshot,
    _observer_dpcli_snapshot,
    _render_dpcli_snapshot_text,
)
from core.nodes._dpcli import _collect_all_nodes


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

    def test_compact_snapshot_only_keeps_small_last_result_evidence(self):
        last_result = {
            "ok": True,
            "session": "unit",
            "action": "batch-detail-extract",
            "data": {
                "items": [
                    {
                        "title": f"Item {index}",
                        "url": f"https://example.test/{index}",
                        "detail_info": {"description": "x" * 1000},
                    }
                    for index in range(100)
                ]
            },
        }

        view = _compact_dpcli_snapshot(SNAPSHOT, last_result)

        self.assertEqual(view["last_result"]["evidence"]["item_count"], 100)
        self.assertEqual(len(view["last_result"]["item_samples"]), 3)
        self.assertEqual(view["last_result"]["items_omitted"], 97)
        self.assertNotIn("detail_info", view["last_result"]["item_samples"][0])

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
        self.assertEqual(command.update["_observer_source"], "dp_cli_full")
        self.assertEqual(command.update["current_url"], "https://example.test")
        self.assertIn("dom_skeleton", command.update)
        self.assertEqual(
            command.update["dpcli_snapshot_view"],
            command.update["dpcli_agent_view"],
        )
        compact_index = command.update["dpcli_snapshot"]["data"]["index"]
        self.assertNotIn("interactable_elements", compact_index)
        self.assertNotIn("surface_index", compact_index)

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
        self.assertEqual(command.update["_observer_source"], "dp_cli_full")
        self.assertEqual(command.update["current_url"], "https://example.test")

    def test_observer_reuses_snapshot_action_result_without_second_cli_call(self):
        with patch("config.DPCLI_OBSERVER_ENABLED", False), \
                patch("config.DPCLI_OBSERVER_FALLBACK_TO_DOM", True), \
                patch("skills.dpcli_executor.DPCLIExecutor") as executor_cls:
            command = _observer_dpcli_snapshot({
                "execution_mode": "dp_cli",
                "dpcli_session": "unit",
                "dpcli_result": SNAPSHOT,
            })

        executor_cls.assert_not_called()
        self.assertEqual(command.goto, "Planner")
        self.assertEqual(command.update["_observer_source"], "dp_cli_full")

    def test_observer_reuses_existing_view_after_non_mutating_extract(self):
        agent_view = {
            "page": {"url": "https://example.test", "title": "Example"},
            "capability_map": {"data_regions": []},
        }
        with patch("config.DPCLI_OBSERVER_ENABLED", False), \
                patch("config.DPCLI_OBSERVER_FALLBACK_TO_DOM", True), \
                patch("skills.dpcli_executor.DPCLIExecutor") as executor_cls:
            command = _observer_dpcli_snapshot({
                "execution_mode": "dp_cli",
                "current_url": "https://example.test",
                "dpcli_session": "unit",
                "dpcli_agent_view": agent_view,
                "dpcli_snapshot_ref": {"snapshot_id": "ss_1"},
                "dpcli_result": {
                    "ok": True,
                    "action": "extract",
                    "data": {"items": [{"title": "One"}]},
                },
            })

        executor_cls.assert_not_called()
        self.assertEqual(command.goto, "Planner")
        self.assertEqual(command.update["_observer_source"], "dp_cli_reuse")
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

    def test_dpcli_execution_snapshot_failure_does_not_fallback_to_other_browser(self):
        failed = {
            "ok": False,
            "session": "unit",
            "action": "snapshot",
            "data": None,
            "error": {"code": "browser_config_error", "message": "bad", "details": {}},
        }
        with patch("config.DPCLI_OBSERVER_ENABLED", False), \
                patch("config.DPCLI_OBSERVER_FALLBACK_TO_DOM", True), \
                patch("skills.dpcli_executor.DPCLIExecutor") as executor_cls:
            executor_cls.return_value.snapshot.return_value = failed
            command = _observer_dpcli_snapshot({"execution_mode": "dp_cli"})

        self.assertIsNotNone(command)
        self.assertEqual(command.goto, "ErrorHandler")
        self.assertEqual(command.update["_observer_source"], "dp_cli")

    def test_collect_all_nodes_merges_duplicate_ref_fields(self):
        snapshot = {
            "data": {
                "index": {
                    "interactable_elements": [
                        {"ref": "e1", "ref_type": "element", "role": "link", "tag": "a"}
                    ],
                    "surface_index": [
                        {"ref": "e1", "text": "Item One", "href": "https://example.test/1"}
                    ],
                    "deep_index": [
                        {"ref": "e1", "name": "Item One", "parent_ref": "r1"}
                    ],
                }
            }
        }

        nodes = _collect_all_nodes(snapshot)

        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["ref_type"], "element")
        self.assertEqual(nodes[0]["role"], "link")
        self.assertEqual(nodes[0]["text"], "Item One")
        self.assertEqual(nodes[0]["href"], "https://example.test/1")
        self.assertEqual(nodes[0]["parent_ref"], "r1")


if __name__ == "__main__":
    unittest.main()
