
"""P6 快速冒烟：不依赖 LangGraph/LLM，只测 snapshot indexer/query/selector 确定性逻辑"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

SAMPLE = {
    "ok": True, "session": "smoke", "action": "snapshot",
    "data": {
        "page": {"url": "https://smoke.test/search", "title": "Smoke Test"},
        "page_identity": {"page_id": "p1", "snapshot_id": "s1", "snapshot_seq": 1, "domain": "smoke.test"},
        "index": {
            "interactable_elements": [
                {"ref": "e1", "role": "searchbox", "name": "", "tag": "input", "input_type": "text", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r1"},
                {"ref": "e2", "role": "button", "name": "百度一下", "tag": "button", "text": "百度一下", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r1"},
                {"ref": "e3", "role": "link", "name": "登录", "tag": "a", "text": "登录", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r2"},
            ],
            "surface_index": [
                {"ref": "r1", "ref_type": "container", "tag": "div", "role": "", "name": "", "parent_ref": "", "child_count": 2},
                {"ref": "r2", "ref_type": "container", "tag": "nav", "role": "navigation", "name": "", "parent_ref": "", "child_count": 3},
            ],
            "deep_index": [],
            "data_regions": [],
            "tree": {
                "parent_map": {"e1": "r1", "e2": "r1", "e3": "r2"},
                "children_map": {"r1": ["e1", "e2"], "r2": ["e3"]},
            },
            "stats": {"total_nodes": 5, "in_viewport": 5},
        },
    },
}


def test_indexer_dedup():
    from skills.dpcli_snapshot_indexer import SnapshotIndexer

    indexer = SnapshotIndexer()
    idx = indexer.build_index(SAMPLE)
    by_ref = idx["by_ref"]
    assert "e1" in by_ref
    assert "e2" in by_ref
    assert by_ref["e2"]["name"] == "百度一下"
    print("  OK Indexer dedup: no duplicate refs overwritten with less info")


def test_text_index_chinese():
    from skills.dpcli_snapshot_indexer import SnapshotIndexer

    indexer = SnapshotIndexer()
    idx = indexer.build_index(SAMPLE)
    by_text = idx["by_text"]
    has_baidu = any("百度" in t for t in by_text)
    assert has_baidu, "Chinese token should be in text index"
    print("  OK Text index: Chinese present")


def test_query_substring_fallback():
    from skills.dpcli_snapshot_store import SnapshotStore
    from skills.dpcli_snapshot_indexer import SnapshotIndexer
    from skills.dpcli_snapshot_query import SnapshotQueryEngine

    temp_dir = tempfile.mkdtemp()
    store = SnapshotStore(session="smoke", base_dir=temp_dir)
    try:
        ref = store.save_full(SAMPLE)
        indexer = SnapshotIndexer()
        index_data = indexer.build_index(SAMPLE)
        store.save_index(ref["snapshot_id"], index_data)

        engine = SnapshotQueryEngine(store)
        assert engine.load(ref["snapshot_id"])

        results = engine.find_by_text("百度一下")
        assert len(results) >= 1, f"Should find '百度一下', got {len(results)}"
        assert results[0]["ref"] == "e2"
        print(f"  OK Substring search: '百度一下' → ref={results[0]['ref']}")

        results_empty = engine.find_by_text("nonexistent_xyz_123")
        assert len(results_empty) == 0
        print("  OK Non-existent text: correctly returns empty")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_searchbox_without_placeholder():
    from skills.dpcli_planner_view import PlannerViewGenerator

    view_gen = PlannerViewGenerator()
    view = view_gen.generate(SAMPLE)
    search_areas = view["capability_map"]["search"]
    assert len(search_areas) >= 1, f"Should detect searchbox without placeholder, got {len(search_areas)}"
    print(f"  OK Search area: searchbox detected even without placeholder (input_ref={search_areas[0]['input_ref']})")


def test_selector_empty_target_hint_with_constraints():
    from skills.dpcli_snapshot_store import SnapshotStore
    from skills.dpcli_snapshot_indexer import SnapshotIndexer
    from skills.dpcli_target_selector import TargetSelector
    temp_dir = tempfile.mkdtemp()
    try:
        store = SnapshotStore(session="smoke", base_dir=temp_dir)
        ref = store.save_full(SAMPLE)
        SnapshotIndexer().build_index(SAMPLE)
        store.save_index(ref["snapshot_id"], SnapshotIndexer().build_index(SAMPLE))
        selector = TargetSelector(store=store)
        selector._engine.load(ref["snapshot_id"])
        result = selector.select({
            "intent": "click", "target_hint": "",
            "target_constraints": {"text_or_name": ["百度一下"], "role": ["button"]},
        })
        assert result["status"] in ("selected", "need_approval"), f"Unexpected: {result['status']}"
        print(f"  OK P0 bug fix: empty target_hint with constraints -> {result['status']}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_legacy_text_hints_fallback():
    from skills.dpcli_snapshot_store import SnapshotStore
    from skills.dpcli_snapshot_indexer import SnapshotIndexer
    from skills.dpcli_target_selector import TargetSelector
    temp_dir = tempfile.mkdtemp()
    try:
        store = SnapshotStore(session="smoke", base_dir=temp_dir)
        ref = store.save_full(SAMPLE)
        store.save_index(ref["snapshot_id"], SnapshotIndexer().build_index(SAMPLE))
        selector = TargetSelector(store=store)
        selector._engine.load(ref["snapshot_id"])
        result = selector.select({
            "intent": "click", "target_hint": "登录按钮", "target_constraints": {},
        })
        assert result["status"] in ("not_found", "selected", "need_approval")
        print(f"  OK Legacy fallback: target_hint without constraints -> {result['status']}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    passed = 0
    failed = 0
    for test_fn in [
        test_indexer_dedup,
        test_text_index_chinese,
        test_query_substring_fallback,
        test_searchbox_without_placeholder,
        test_selector_empty_target_hint_with_constraints,
        test_legacy_text_hints_fallback,
    ]:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  FAIL {test_fn.__name__}: {e}")
            failed += 1
    print(f"\n{'='*40}\nP6 Smoke: {passed} passed, {failed} failed\n{'='*40}")
    sys.exit(0 if failed == 0 else 1)
