import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tests.unit.stubs  # noqa: F401

import config
import skills.action_cache as action_cache_module
from core.nodes.cache_lookup import cache_lookup_node
from skills.action_cache import ActionCacheManager


class DPCLIActionCacheIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.snapshot_view = {
            "page": {
                "url": "https://example.test/catalog",
                "title": "Product catalog",
                "domain": "example.test",
            },
            "capability_map": {
                "data_regions": [
                    {
                        "ref": "r78",
                        "kind": "list",
                        "name": "Products",
                        "tag": "ol",
                        "item_count": 20,
                        "samples": [
                            {
                                "ref": "e54",
                                "text": "Product One",
                                "url": "https://example.test/product/1",
                            }
                        ],
                        "available_actions": [
                            "expand",
                            "list-items",
                            "extract",
                        ],
                    }
                ],
                "pagination": [
                    {
                        "group_id": "g_next",
                        "controls": [
                            {
                                "ref": "e254",
                                "label": "next",
                                "direction": "next",
                                "enabled": True,
                            }
                        ],
                    }
                ],
            },
        }
        self.action = {
            "skill": "extract",
            "params": {
                "ref": "r78",
                "schema": ["title", "url"],
            },
        }

    def _save(self, manager):
        return manager.save(
            user_task="Extract product titles and URLs",
            goal="Extract the product list",
            url="https://example.test/catalog",
            action=self.action,
            snapshot_view=self.snapshot_view,
            result_summary="20 rows",
        )

    def _page_two_view(self):
        view = copy.deepcopy(self.snapshot_view)
        region = view["capability_map"]["data_regions"][0]
        region["ref"] = "r999"
        region["name"] = "Products page two"
        region["samples"][0] = {
            "ref": "e999",
            "text": "Product Twenty-One",
            "url": "https://example.test/product/21",
        }
        view["capability_map"]["pagination"][0]["group_id"] = "g_next_2"
        view["capability_map"]["pagination"][0]["controls"][0]["ref"] = "e1000"
        return view

    def test_planner_view_signature_produces_high_confidence_hit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "actions.json"
            manager = ActionCacheManager(str(path))
            cache_id = self._save(manager)

            hits = manager.search(
                user_task="Extract product titles and URLs",
                goal="Extract the product list",
                url="https://example.test/catalog?page=2",
                snapshot_view=self._page_two_view(),
            )

            self.assertTrue(hits)
            self.assertEqual(hits[0].id, cache_id)
            self.assertGreaterEqual(hits[0].score, 0.75)
            signature = json.loads(
                path.read_text(encoding="utf-8")
            )[0]["snapshot_signature"]
            self.assertIn("capability_map", signature)
            self.assertNotIn("e54", signature)
            self.assertNotIn("Product One", signature)

    def test_cache_lookup_routes_full_snapshot_hit_to_dpcli_executor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ActionCacheManager(str(Path(temp_dir) / "actions.json"))
            cache_id = self._save(manager)

            with (
                patch.object(config, "CODE_CACHE_ENABLED", False),
                patch.object(config, "DPCLI_ENABLED", True),
                patch.object(config, "ACTION_CACHE_ENABLED", True),
                patch.object(config, "ACTION_CACHE_THRESHOLD", 0.75),
                patch.object(
                    action_cache_module,
                    "action_cache_manager",
                    manager,
                ),
            ):
                command = cache_lookup_node(
                    {
                        "user_task": "Extract product titles and URLs",
                        "plan": "Extract the product list",
                        "current_url": "https://example.test/catalog?page=2",
                        "dpcli_snapshot_view": self._page_two_view(),
                        "_failed_action_cache_ids": [],
                    },
                    {},
                )

            self.assertEqual(command.goto, "Executor")
            self.assertEqual(command.update["execution_mode"], "dp_cli")
            self.assertEqual(command.update["_action_source"], "action_cache")
            self.assertEqual(command.update["_action_cache_hit_id"], cache_id)
            self.assertEqual(
                command.update["generated_action"]["params"]["ref"],
                "r999",
            )

    def test_cache_hit_is_skipped_when_target_cannot_be_rebound(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ActionCacheManager(str(Path(temp_dir) / "actions.json"))
            self._save(manager)
            incompatible_view = self._page_two_view()
            incompatible_view["capability_map"]["data_regions"][0].update(
                {
                    "kind": "table",
                    "tag": "table",
                    "available_actions": ["extract"],
                }
            )

            hits = manager.search(
                user_task="Extract product titles and URLs",
                goal="Extract the product list",
                url="https://example.test/catalog?page=2",
                snapshot_view=incompatible_view,
            )

            self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
