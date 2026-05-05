import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skills.dpcli_snapshot_store import SnapshotStore
from skills.dpcli_snapshot_indexer import SnapshotIndexer
from skills.dpcli_planner_view import PlannerViewGenerator
from skills.dpcli_snapshot_query import SnapshotQueryEngine
from core.nodes.target_selector import TargetSelector

# ═══════════════════════════════════════════════════════════
# 测试数据
# ═══════════════════════════════════════════════════════════

SAMPLE_SNAPSHOT = {
    "ok": True,
    "session": "test",
    "action": "snapshot",
    "data": {
        "page": {"url": "https://example.com/search", "title": "Example Search"},
        "page_identity": {
            "page_id": "page_001",
            "snapshot_id": "snap_001",
            "snapshot_seq": 1,
            "runtime_id": "rt_001",
            "domain": "example.com",
        },
        "index": {
            "interactable_elements": [
                {"ref": "e1", "role": "searchbox", "name": "search", "tag": "input", "text": "", "input_type": "text", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r1"},
                {"ref": "e2", "role": "button", "name": "搜索", "tag": "button", "text": "搜索", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r1"},
                {"ref": "e3", "role": "link", "name": "首页", "tag": "a", "text": "首页", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r2"},
                {"ref": "e4", "role": "link", "name": "下一页", "tag": "a", "text": "下一页", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r3"},
                {"ref": "e5", "role": "link", "name": "2", "tag": "a", "text": "2", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r3"},
                {"ref": "e6", "role": "link", "name": "上一页", "tag": "a", "text": "上一页", "in_viewport": False, "interactable_now": False, "ref_type": "element", "parent_ref": "r3"},
                {"ref": "e7", "role": "link", "name": "Item 1", "tag": "a", "text": "Item 1", "href": "/item/1", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r4"},
                {"ref": "e8", "role": "link", "name": "Item 2", "tag": "a", "text": "Item 2", "href": "/item/2", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r4"},
                {"ref": "e9", "role": "link", "name": "Item 3", "tag": "a", "text": "Item 3", "href": "/item/3", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r4"},
                {"ref": "e10", "role": "link", "name": "Item 4", "tag": "a", "text": "Item 4", "href": "/item/4", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r4"},
            ],
            "surface_index": [
                {"ref": "r1", "ref_type": "container", "tag": "form", "role": "form", "name": "search_form", "text": "", "parent_ref": "", "in_viewport": True, "interactable_now": False, "child_count": 2},
                {"ref": "r2", "ref_type": "container", "tag": "nav", "role": "navigation", "name": "main_nav", "text": "", "parent_ref": "", "in_viewport": True, "interactable_now": False, "child_count": 5},
                {"ref": "r3", "ref_type": "container", "tag": "div", "role": "", "name": "pagination", "text": "", "parent_ref": "", "in_viewport": True, "interactable_now": False, "child_count": 3},
                {"ref": "r4", "ref_type": "container", "tag": "ul", "role": "list", "name": "results", "text": "", "parent_ref": "", "in_viewport": True, "interactable_now": False, "child_count": 4, "item_count": 4},
            ],
            "deep_index": [],
            "data_regions": [
                {"ref": "r4", "ref_type": "container", "tag": "ul", "role": "list", "name": "results", "kind": "list", "item_count": 4, "sample_items": [{"ref": "e7", "text": "Item 1"}], "score": 5, "why": "list container with 4 items"},
            ],
            "tree": {
                "roots": ["r1", "r2", "r3", "r4"],
                "parent_map": {"e1": "r1", "e2": "r1", "e3": "r2", "e4": "r3", "e5": "r3", "e6": "r3", "e7": "r4", "e8": "r4", "e9": "r4", "e10": "r4"},
                "children_map": {"r1": ["e1", "e2"], "r2": ["e3"], "r3": ["e4", "e5", "e6"], "r4": ["e7", "e8", "e9", "e10"]},
            },
            "stats": {"total_nodes": 14, "surface_count": 4, "deep_count": 0, "in_viewport": 13, "offscreen": 1, "interactable_now": 9},
        },
    },
}


# ═══════════════════════════════════════════════════════════
# SnapshotStore 测试
# ═══════════════════════════════════════════════════════════

class TestSnapshotStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.store = SnapshotStore(session="test", base_dir=self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_save_full_creates_artifacts(self):
        ref = self.store.save_full(SAMPLE_SNAPSHOT)
        self.assertIsNotNone(ref)
        self.assertTrue(ref["snapshot_id"].startswith("ss_"))
        self.assertEqual(ref["session"], "test")
        self.assertEqual(ref["page_url"], "https://example.com/search")
        self.assertEqual(ref["page_title"], "Example Search")

    def test_save_full_writes_json_files(self):
        ref = self.store.save_full(SAMPLE_SNAPSHOT)
        sid = ref["snapshot_id"]
        full_path = Path(ref["full_snapshot_file"])
        self.assertTrue(full_path.exists())

        loaded = self.store.load_full(sid)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["data"]["page"]["url"], "https://example.com/search")

    def test_save_index_and_compressed(self):
        ref = self.store.save_full(SAMPLE_SNAPSHOT)
        sid = ref["snapshot_id"]
        index_path = self.store.save_index(sid, {"test": True})
        self.assertTrue(Path(index_path).exists())

        comp_path = self.store.save_compressed_index(sid, {"groups": []})
        self.assertTrue(Path(comp_path).exists())

        view_path = self.store.save_planner_view(sid, {"page": {}})
        self.assertTrue(Path(view_path).exists())

    def test_list_snapshots(self):
        self.store.save_full(SAMPLE_SNAPSHOT)
        self.store.save_full(SAMPLE_SNAPSHOT)
        metas = self.store.list_snapshots()
        self.assertEqual(len(metas), 2)

    def test_latest_snapshot_id(self):
        self.store.save_full(SAMPLE_SNAPSHOT)
        latest = self.store.latest_snapshot_id()
        self.assertIsNotNone(latest)
        self.assertTrue(latest.startswith("ss_"))

    def test_load_by_file_path(self):
        ref = self.store.save_full(SAMPLE_SNAPSHOT)
        loaded = self.store.load_by_file_path(ref["full_snapshot_file"])
        self.assertIsNotNone(loaded)

    def test_snapshot_seq_increments(self):
        ref1 = self.store.save_full(SAMPLE_SNAPSHOT)
        ref2 = self.store.save_full(SAMPLE_SNAPSHOT)
        self.assertEqual(ref2["snapshot_seq"], ref1["snapshot_seq"] + 1)


# ═══════════════════════════════════════════════════════════
# SnapshotIndexer 测试
# ═══════════════════════════════════════════════════════════

class TestSnapshotIndexer(unittest.TestCase):
    def setUp(self):
        self.indexer = SnapshotIndexer()

    def test_build_index_creates_all_lookups(self):
        index = self.indexer.build_index(SAMPLE_SNAPSHOT)
        self.assertIn("by_ref", index)
        self.assertIn("by_role", index)
        self.assertIn("by_text", index)
        self.assertIn("by_region", index)
        self.assertIn("by_parent", index)
        self.assertIn("by_tag", index)
        self.assertIn("summary", index)

    def test_by_ref_lookup(self):
        index = self.indexer.build_index(SAMPLE_SNAPSHOT)
        by_ref = index["by_ref"]
        self.assertIn("e1", by_ref)
        self.assertEqual(by_ref["e1"]["role"], "searchbox")

    def test_by_role_groups_correctly(self):
        index = self.indexer.build_index(SAMPLE_SNAPSHOT)
        by_role = index["by_role"]
        self.assertIn("link", by_role)
        self.assertTrue(len(by_role["link"]) >= 7)

    def test_by_text_indexes_tokens(self):
        index = self.indexer.build_index(SAMPLE_SNAPSHOT)
        by_text = index["by_text"]
        # "搜索" should be indexed from e2
        self.assertIn("搜索", by_text)

    def test_by_region_maps_children(self):
        index = self.indexer.build_index(SAMPLE_SNAPSHOT)
        by_region = index["by_region"]
        self.assertIn("r4", by_region)
        self.assertTrue(len(by_region["r4"]) >= 2)

    def test_summary_stats(self):
        index = self.indexer.build_index(SAMPLE_SNAPSHOT)
        summary = index["summary"]
        self.assertGreater(summary["elements"], 0)
        self.assertGreater(summary["containers"], 0)
        self.assertGreater(summary["buttons"], 0)
        self.assertGreater(summary["links"], 0)

    def test_structural_hash_same_structure(self):
        h1 = self.indexer._compute_structural_hash({"tag": "a", "role": "link", "ref_type": "element"})
        h2 = self.indexer._compute_structural_hash({"tag": "a", "role": "link", "ref_type": "element"})
        self.assertEqual(h1, h2)

    def test_structural_hash_different_structure(self):
        h1 = self.indexer._compute_structural_hash({"tag": "a", "role": "link", "ref_type": "element"})
        h2 = self.indexer._compute_structural_hash({"tag": "button", "role": "button", "ref_type": "element"})
        self.assertNotEqual(h1, h2)

    def test_build_compressed_index_groups_similar(self):
        nodes = [
            {"ref": "e1", "tag": "a", "role": "link", "ref_type": "element", "parent_ref": "r1", "text": "Item 1"},
            {"ref": "e2", "tag": "a", "role": "link", "ref_type": "element", "parent_ref": "r1", "text": "Item 2"},
            {"ref": "e3", "tag": "a", "role": "link", "ref_type": "element", "parent_ref": "r1", "text": "Item 3"},
            {"ref": "e4", "tag": "a", "role": "link", "ref_type": "element", "parent_ref": "r1", "text": "Item 4"},
        ]
        compressed = self.indexer.build_compressed_index(nodes, min_group_size=3)
        self.assertGreater(len(compressed), 0)
        group = compressed[0]
        self.assertEqual(group["count"], 4)
        self.assertIn("_ref", group["data"])

    def test_compressed_group_ref_traceability(self):
        nodes = [
            {"ref": "e10", "tag": "a", "role": "link", "ref_type": "element", "parent_ref": "r1", "text": "A"},
            {"ref": "e11", "tag": "a", "role": "link", "ref_type": "element", "parent_ref": "r1", "text": "B"},
            {"ref": "e12", "tag": "a", "role": "link", "ref_type": "element", "parent_ref": "r1", "text": "C"},
        ]
        compressed = self.indexer.build_compressed_index(nodes, min_group_size=3)
        self.assertEqual(len(compressed), 1)
        self.assertEqual(compressed[0]["data"]["_ref"], ["e10", "e11", "e12"])
        self.assertEqual(compressed[0]["data"]["_index"], [10, 11, 12])


# ═══════════════════════════════════════════════════════════
# PlannerViewGenerator 测试
# ═══════════════════════════════════════════════════════════

class TestPlannerViewGenerator(unittest.TestCase):
    def setUp(self):
        self.generator = PlannerViewGenerator()

    def test_generate_agent_view_structure(self):
        view = self.generator.generate(SAMPLE_SNAPSHOT)
        self.assertIn("page", view)
        self.assertIn("focus", view)
        self.assertIn("capability_map", view)
        self.assertIn("top_level_groups", view)
        self.assertIn("coverage", view)
        self.assertIn("planner_instructions", view)

    def test_page_info(self):
        view = self.generator.generate(SAMPLE_SNAPSHOT)
        self.assertEqual(view["page"]["url"], "https://example.com/search")
        self.assertEqual(view["page"]["title"], "Example Search")

    def test_capability_map_has_search(self):
        view = self.generator.generate(SAMPLE_SNAPSHOT)
        search = view["capability_map"]["search"]
        self.assertGreater(len(search), 0)
        self.assertEqual(search[0]["kind"], "search_area")

    def test_capability_map_has_pagination(self):
        view = self.generator.generate(SAMPLE_SNAPSHOT)
        pagination = view["capability_map"]["pagination"]
        self.assertGreater(len(pagination), 0)

    def test_capability_map_has_navigation(self):
        view = self.generator.generate(SAMPLE_SNAPSHOT)
        nav = view["capability_map"]["navigation"]
        self.assertGreater(len(nav), 0)

    def test_capability_map_has_data_regions(self):
        view = self.generator.generate(SAMPLE_SNAPSHOT)
        regions = view["capability_map"]["data_regions"]
        self.assertGreater(len(regions), 0)
        self.assertEqual(regions[0]["kind"], "list")

    def test_top_level_groups_have_search(self):
        view = self.generator.generate(SAMPLE_SNAPSHOT)
        groups = view["top_level_groups"]
        kinds = [g["kind"] for g in groups]
        self.assertIn("search_area", kinds)

    def test_top_level_groups_have_pagination(self):
        view = self.generator.generate(SAMPLE_SNAPSHOT)
        groups = view["top_level_groups"]
        kinds = [g["kind"] for g in groups]
        self.assertIn("pagination", kinds)

    def test_top_level_groups_have_repeated_data(self):
        view = self.generator.generate(SAMPLE_SNAPSHOT)
        groups = view["top_level_groups"]
        kinds = [g["kind"] for g in groups]
        self.assertIn("repeated_data_items", kinds)

    def test_coverage_correct(self):
        view = self.generator.generate(SAMPLE_SNAPSHOT)
        coverage = view["coverage"]
        self.assertGreater(coverage["total_interactables"], 0)
        self.assertEqual(coverage["total_data_regions"], 1)

    def test_planner_instructions(self):
        view = self.generator.generate(SAMPLE_SNAPSHOT)
        self.assertGreater(len(view["planner_instructions"]), 0)

    def test_generate_diagnostics(self):
        diag = self.generator.generate_diagnostics(SAMPLE_SNAPSHOT)
        self.assertTrue(diag["snapshot_ok"])
        self.assertGreater(diag["raw_nodes"], 0)
        self.assertGreater(diag["interactables"], 0)
        self.assertGreater(diag["data_regions_detected"], 0)
        self.assertTrue(diag["coverage"]["full_snapshot_preserved"])
        self.assertTrue(diag["coverage"]["planner_view_lossy"])
        self.assertTrue(diag["coverage"]["recoverable_from_full_snapshot"])

    def test_pagination_detection(self):
        view = self.generator.generate(SAMPLE_SNAPSHOT)
        pagination = view["capability_map"]["pagination"]
        self.assertTrue(len(pagination) > 0)
        controls = pagination[0].get("controls", [])
        directions = [c["direction"] for c in controls]
        self.assertIn("next", directions)
        self.assertIn("prev", directions)

    def test_data_region_actions(self):
        view = self.generator.generate(SAMPLE_SNAPSHOT)
        regions = view["capability_map"]["data_regions"]
        actions = regions[0].get("available_actions", [])
        self.assertIn("extract", actions)

    def test_primary_actions_detected(self):
        view = self.generator.generate(SAMPLE_SNAPSHOT)
        primary = view["capability_map"]["primary_actions"]
        self.assertTrue(len(primary) > 0)

    def test_dialogs_empty_on_basic_page(self):
        view = self.generator.generate(SAMPLE_SNAPSHOT)
        dialogs = view["capability_map"]["dialogs"]
        self.assertEqual(len(dialogs), 0)


# ═══════════════════════════════════════════════════════════
# SnapshotQueryEngine 测试
# ═══════════════════════════════════════════════════════════

class TestSnapshotQueryEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.store = SnapshotStore(session="test", base_dir=self.temp_dir)
        self.indexer = SnapshotIndexer()

        self.ref = self.store.save_full(SAMPLE_SNAPSHOT)
        sid = self.ref["snapshot_id"]
        index_data = self.indexer.build_index(SAMPLE_SNAPSHOT)
        nodes = (
            list(SAMPLE_SNAPSHOT["data"]["index"]["interactable_elements"])
            + list(SAMPLE_SNAPSHOT["data"]["index"]["surface_index"])
            + list(SAMPLE_SNAPSHOT["data"]["index"]["deep_index"])
        )
        compressed = self.indexer.build_compressed_index(nodes)
        self.store.save_index(sid, index_data)
        self.store.save_compressed_index(sid, {"groups": compressed})

        self.engine = SnapshotQueryEngine(self.store)
        self.engine.load(sid)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_search_snapshot_by_role(self):
        results = self.engine.search_snapshot({"role": "button"})
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertEqual(r["role"], "button")

    def test_search_snapshot_by_text(self):
        results = self.engine.search_snapshot({"text": "搜索"})
        self.assertGreater(len(results), 0)

    def test_search_snapshot_by_tag(self):
        results = self.engine.search_snapshot({"tag": "input"})
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["tag"], "input")

    def test_get_ref(self):
        node = self.engine.get_ref("e1")
        self.assertIsNotNone(node)
        self.assertEqual(node["role"], "searchbox")

    def test_get_ref_nonexistent(self):
        node = self.engine.get_ref("e999")
        self.assertIsNone(node)

    def test_get_region(self):
        region = self.engine.get_region("r4")
        self.assertIsNotNone(region)
        self.assertEqual(region["kind"], "list")

    def test_find_by_text(self):
        results = self.engine.find_by_text("搜索")
        self.assertGreater(len(results), 0)

    def test_find_near(self):
        results = self.engine.find_near("e1", {"role": "button"})
        self.assertGreater(len(results), 0)

    def test_verify_ref(self):
        result = self.engine.verify_ref("e2", "click")
        self.assertTrue(result["valid"])

    def test_verify_ref_invalid_intent(self):
        result = self.engine.verify_ref("r4", "click")
        self.assertFalse(result["valid"])

    def test_verify_ref_nonexistent(self):
        result = self.engine.verify_ref("e999", "click")
        self.assertFalse(result["valid"])

    def test_get_region_refs(self):
        refs = self.engine.get_region_refs("r4")
        self.assertGreater(len(refs), 0)
        self.assertIn("e7", refs)

    def test_load_subtree(self):
        subtree = self.engine.load_subtree("r1", depth=1)
        self.assertIn("node", subtree)
        self.assertIn("children", subtree)

    def test_is_loaded(self):
        self.assertTrue(self.engine.is_loaded)

    def test_not_loaded_by_default(self):
        engine2 = SnapshotQueryEngine(self.store)
        self.assertFalse(engine2.is_loaded)


# ═══════════════════════════════════════════════════════════
# TargetSelector 测试
# ═══════════════════════════════════════════════════════════

class TestTargetSelector(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.store = SnapshotStore(session="test", base_dir=self.temp_dir)
        self.indexer = SnapshotIndexer()

        self.ref = self.store.save_full(SAMPLE_SNAPSHOT)
        sid = self.ref["snapshot_id"]
        index_data = self.indexer.build_index(SAMPLE_SNAPSHOT)
        nodes = (
            list(SAMPLE_SNAPSHOT["data"]["index"]["interactable_elements"])
            + list(SAMPLE_SNAPSHOT["data"]["index"]["surface_index"])
            + list(SAMPLE_SNAPSHOT["data"]["index"]["deep_index"])
        )
        compressed = self.indexer.build_compressed_index(nodes)
        self.store.save_index(sid, index_data)
        self.store.save_compressed_index(sid, {"groups": compressed})

        self.selector = TargetSelector(store=self.store)
        self.selector._engine.load(sid)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_select_search_button(self):
        result = self.selector.select({
            "intent": "click",
            "target_hint": "搜索按钮",
            "target_constraints": {"role": ["button"], "text_or_name": ["搜索"]},
        })
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["target_ref"], "e2")
        self.assertEqual(result["selection_mode"], "deterministic")

    def test_select_search_input(self):
        result = self.selector.select({
            "intent": "type",
            "target_hint": "搜索输入框",
            "target_constraints": {"role": ["searchbox", "textbox"]},
        })
        self.assertEqual(result["status"], "selected")

    def test_select_nonexistent_target(self):
        result = self.selector.select({
            "intent": "click",
            "target_hint": "不存在的按钮",
            "target_constraints": {},
        })
        self.assertEqual(result["status"], "not_found")

    def test_select_with_near_constraint(self):
        result = self.selector.select({
            "intent": "click",
            "target_hint": "搜索按钮",
            "target_constraints": {"role": ["button"], "near": "search"},
        })
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["target_ref"], "e2")

    def test_select_multiple_candidates_need_approval(self):
        result = self.selector.select({
            "intent": "click",
            "target_hint": "Item",
            "target_constraints": {"role": ["link"]},
        })
        self.assertIn(result["status"], ["need_approval", "selected"])

    def test_verify_selection(self):
        result = self.selector.verify_selection("e2", "click")
        self.assertEqual(result["status"], "selected")
        self.assertTrue(result["evidence"]["verified"])

    def test_verify_selection_invalid(self):
        result = self.selector.verify_selection("e999", "click")
        self.assertEqual(result["status"], "not_found")

    def test_select_from_structured_plan(self):
        plan = {
            "status": "continue",
            "intent": "click",
            "target_hint": "搜索按钮",
            "target_constraints": {"role": ["button"], "text_or_name": ["搜索"]},
        }
        result = self.selector.select_from_structured_plan(plan)
        self.assertEqual(result["status"], "selected")

    def test_select_with_snapshot_ref(self):
        result = self.selector.select({
            "intent": "click",
            "target_hint": "搜索按钮",
            "target_constraints": {"role": ["button"], "text_or_name": ["搜索"]},
        }, snapshot_ref=self.ref)
        self.assertEqual(result["status"], "selected")

    def test_select_pagination_next(self):
        result = self.selector.select({
            "intent": "click",
            "target_hint": "下一页",
            "target_constraints": {"role": ["link", "button"], "text_or_name": ["下一页", "next"]},
        })
        self.assertEqual(result["status"], "selected")


if __name__ == "__main__":
    unittest.main()
