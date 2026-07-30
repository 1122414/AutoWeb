import tempfile
import unittest
from pathlib import Path

from skills.action_cache import ActionCacheManager


class ActionCacheTests(unittest.TestCase):
    def test_save_and_search_action(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ActionCacheManager(str(Path(temp_dir) / "actions.json"))
            cache_id = manager.save(
                user_task="点击搜索按钮",
                goal="点击搜索按钮",
                url="https://example.test/search",
                action={"skill": "click", "params": {"ref": "e1"}},
                snapshot_view={"interactable_elements": [{"ref": "e1", "name": "搜索"}]},
                result_summary="clicked",
            )

            hits = manager.search(
                user_task="点击搜索按钮",
                goal="点击搜索按钮",
                url="https://example.test/other",
                snapshot_view={"interactable_elements": [{"ref": "e1", "name": "搜索"}]},
            )

            self.assertEqual(hits[0].id, cache_id)
            self.assertEqual(hits[0].action["skill"], "click")

    def test_record_failure_updates_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "actions.json"
            manager = ActionCacheManager(str(path))
            cache_id = manager.save(
                user_task="task",
                goal="goal",
                url="https://example.test",
                action={"skill": "snapshot", "params": {}},
            )

            manager.record_failure(cache_id, "ref_stale")
            text = path.read_text(encoding="utf-8")

            self.assertIn('"failure_count": 1', text)
            self.assertIn("ref_stale", text)

    def test_unverified_or_repeatedly_failed_actions_are_not_reused(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ActionCacheManager(str(Path(temp_dir) / "actions.json"))
            unverified = manager.save(
                user_task="extract products",
                goal="extract title",
                url="https://example.test/products",
                action={"skill": "extract", "params": {"target_ref": "r1"}},
                snapshot_view={"data_regions": [{"ref": "r1", "name": "products"}]},
                verification_evidence={"is_success": False},
            )
            failed = manager.save(
                user_task="extract products",
                goal="extract title",
                url="https://example.test/products",
                action={"skill": "extract", "params": {"target_ref": "r1"}},
                snapshot_view={"data_regions": [{"ref": "r1", "name": "products"}]},
                verification_evidence={"is_success": True},
            )
            manager.record_failure(failed, "stale")
            manager.record_failure(failed, "stale")

            hits = manager.search(
                user_task="extract products",
                goal="extract title",
                url="https://example.test/other",
                snapshot_view={"data_regions": [{"ref": "r1", "name": "products"}]},
            )

            self.assertNotIn(unverified, [hit.id for hit in hits])
            self.assertNotIn(failed, [hit.id for hit in hits])


if __name__ == "__main__":
    unittest.main()
