from __future__ import annotations

import tempfile
import unittest

from skills.dpcli_snapshot_store import SnapshotStore
from skills.dpcli_target_selector import TargetSelector


class TestDpcliSnapshotSession(unittest.TestCase):
    def test_target_selector_loads_snapshot_ref_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_store = SnapshotStore(session="autoweb-debug", base_dir=temp_dir)
            snapshot_ref = source_store.save_full(
                {"data": {"page": {"url": "https://example.test", "title": "Example"}}}
            )
            node = {
                "ref": "e1",
                "ref_type": "element",
                "role": "button",
                "tag": "button",
                "text": "Search",
                "name": "Search",
                "interactable_now": True,
                "in_viewport": True,
            }
            source_store.save_index(
                snapshot_ref["snapshot_id"],
                {
                    "by_ref": {"e1": node},
                    "by_role": {"button": [node]},
                    "by_text": {},
                    "by_region": {},
                    "by_parent": {},
                    "tree": {},
                    "regions": [],
                },
            )
            source_store.save_compressed_index(snapshot_ref["snapshot_id"], {"groups": []})

            wrong_session_store = SnapshotStore(session="autoweb", base_dir=temp_dir)
            selector = TargetSelector(store=wrong_session_store)

            result = selector.select(
                {
                    "intent": "click",
                    "target_hint": "Search",
                    "target_constraints": {"role": ["button"]},
                },
                snapshot_ref=snapshot_ref,
            )

            self.assertEqual(result["status"], "selected")
            self.assertEqual(result["target_ref"], "e1")


if __name__ == "__main__":
    unittest.main()
