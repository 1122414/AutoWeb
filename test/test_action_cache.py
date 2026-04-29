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


if __name__ == "__main__":
    unittest.main()
