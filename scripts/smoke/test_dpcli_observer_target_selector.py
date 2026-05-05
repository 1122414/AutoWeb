"""
dp_cli Observer + TargetSelector 功能测试脚本

运行前请确保已安装依赖: pip install -r requirements.txt
无需浏览器或 dp_cli 进程 - 使用模拟数据测试完整流程。

使用方式:
    python scripts/test_dpcli_observer_target_selector.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_full_pipeline():
    """测试完整流程: SnapshotStore → Indexer → PlannerView → QueryEngine → TargetSelector"""
    import shutil
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from skills.dpcli_snapshot_store import SnapshotStore
    from skills.dpcli_snapshot_indexer import SnapshotIndexer
    from skills.dpcli_planner_view import PlannerViewGenerator
    from skills.dpcli_snapshot_query import SnapshotQueryEngine
    from core.nodes.target_selector import TargetSelector

    SAMPLE_SNAPSHOT = {
        "ok": True, "session": "test", "action": "snapshot",
        "data": {
            "page": {"url": "https://example.com/search", "title": "Example Search Page"},
            "page_identity": {"page_id": "page_001", "snapshot_id": "snap_001", "snapshot_seq": 1, "domain": "example.com"},
            "index": {
                "interactable_elements": [
                    {"ref": "e1", "role": "searchbox", "name": "search", "tag": "input", "input_type": "text", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r1"},
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
                    {"ref": "r1", "ref_type": "container", "tag": "form", "role": "form", "name": "search_form", "parent_ref": "", "in_viewport": True, "interactable_now": False, "child_count": 2},
                    {"ref": "r2", "ref_type": "container", "tag": "nav", "role": "navigation", "name": "main_nav", "parent_ref": "", "in_viewport": True, "interactable_now": False, "child_count": 5},
                    {"ref": "r3", "ref_type": "container", "tag": "div", "role": "", "name": "pagination", "parent_ref": "", "in_viewport": True, "interactable_now": False, "child_count": 3},
                    {"ref": "r4", "ref_type": "container", "tag": "ul", "role": "list", "name": "results", "parent_ref": "", "in_viewport": True, "interactable_now": False, "child_count": 4, "item_count": 4},
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

    passed = 0
    failed = 0

    # ═══ 阶段1: SnapshotStore ═══
    print("\n" + "=" * 60)
    print("阶段1: SnapshotStore - 快照落盘测试")
    print("=" * 60)

    temp_dir = tempfile.mkdtemp()
    store = SnapshotStore(session="functional_test", base_dir=temp_dir)

    try:
        ref = store.save_full(SAMPLE_SNAPSHOT)
        assert ref["snapshot_id"].startswith("ss_"), "snapshot_id should start with ss_"
        assert ref["session"] == "functional_test"
        print(f"  ✓ 保存 full snapshot: {ref['snapshot_id']}")
        passed += 1

        loaded = store.load_full(ref["snapshot_id"])
        assert loaded is not None, "load_full should return data"
        assert loaded["data"]["page"]["url"] == "https://example.com/search"
        print(f"  ✓ 加载 full snapshot: ok")
        passed += 1

        indexer = SnapshotIndexer()
        index_data = indexer.build_index(SAMPLE_SNAPSHOT)
        store.save_index(ref["snapshot_id"], index_data)
        print(f"  ✓ 保存 index: {len(index_data.get('by_ref', {}))} refs indexed")
        passed += 1

        all_nodes = (
            list(SAMPLE_SNAPSHOT["data"]["index"]["interactable_elements"])
            + list(SAMPLE_SNAPSHOT["data"]["index"]["surface_index"])
            + list(SAMPLE_SNAPSHOT["data"]["index"]["deep_index"])
        )
        compressed = indexer.build_compressed_index(all_nodes)
        store.save_compressed_index(ref["snapshot_id"], {"groups": compressed})
        print(f"  ✓ 保存 compressed index: {len(compressed)} groups")
        passed += 1

        view_gen = PlannerViewGenerator()
        agent_view = view_gen.generate(SAMPLE_SNAPSHOT, index_data, compressed)
        store.save_planner_view(ref["snapshot_id"], agent_view)
        print(f"  ✓ 保存 planner view: {len(agent_view.get('capability_map', {}))} capabilities")
        passed += 1

    except Exception as e:
        print(f"  ✗ FAIL: {e}")
        failed += 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    # ═══ 阶段2: PlannerView ═══
    print("\n" + "=" * 60)
    print("阶段2: PlannerViewGenerator - 页面能力地图测试")
    print("=" * 60)

    try:
        view_gen = PlannerViewGenerator()
        view = view_gen.generate(SAMPLE_SNAPSHOT)
        cap = view["capability_map"]

        assert len(cap["search"]) > 0, "should detect search area"
        print(f"  ✓ 搜索区域检测: {len(cap['search'])} 个")
        passed += 1

        assert len(cap["pagination"]) > 0, "should detect pagination"
        print(f"  ✓ 分页控件检测: {len(cap['pagination'])} 组")
        passed += 1

        assert len(cap["data_regions"]) > 0, "should detect data regions"
        print(f"  ✓ 数据区域检测: {cap['data_regions'][0]['kind']} (item_count={cap['data_regions'][0]['item_count']})")
        passed += 1

        assert len(cap["navigation"]) > 0, "should detect navigation"
        print(f"  ✓ 导航检测: {len(cap['navigation'])} 个")
        passed += 1

        diag = view_gen.generate_diagnostics(SAMPLE_SNAPSHOT)
        assert diag["coverage"]["full_snapshot_preserved"] is True
        print(f"  ✓ 诊断: snapshot_ok={diag['snapshot_ok']}, data_regions={diag['data_regions_detected']}")
        passed += 1

    except Exception as e:
        print(f"  ✗ FAIL: {e}")
        failed += 1

    # ═══ 阶段3: SnapshotQueryEngine ═══
    print("\n" + "=" * 60)
    print("阶段3: SnapshotQueryEngine - 本地查询测试")
    print("=" * 60)

    temp_dir = tempfile.mkdtemp()
    store = SnapshotStore(session="functional_test", base_dir=temp_dir)
    try:
        ref = store.save_full(SAMPLE_SNAPSHOT)
        sid = ref["snapshot_id"]
        indexer = SnapshotIndexer()
        index_data = indexer.build_index(SAMPLE_SNAPSHOT)
        store.save_index(sid, index_data)

        all_nodes = (
            list(SAMPLE_SNAPSHOT["data"]["index"]["interactable_elements"])
            + list(SAMPLE_SNAPSHOT["data"]["index"]["surface_index"])
            + list(SAMPLE_SNAPSHOT["data"]["index"]["deep_index"])
        )
        compressed = indexer.build_compressed_index(all_nodes)
        store.save_compressed_index(sid, {"groups": compressed})

        engine = SnapshotQueryEngine(store)
        assert engine.load(sid), "engine should load"

        results = engine.search_snapshot({"role": "button"})
        assert len(results) > 0, "should find buttons"
        print(f"  ✓ 按 role 搜索: {len(results)} 个按钮")
        passed += 1

        results = engine.search_snapshot({"tag": "input"})
        assert len(results) == 1, "should find 1 input"
        print(f"  ✓ 按 tag 搜索: {results[0]['ref']} ({results[0]['role']})")
        passed += 1

        node = engine.get_ref("e2")
        assert node is not None and node["name"] == "搜索"
        print(f"  ✓ get_ref: e2 → {node['name']} ({node['role']})")
        passed += 1

        region = engine.get_region("r4")
        assert region is not None and region["kind"] == "list"
        print(f"  ✓ get_region: r4 → {region['kind']} ({region['item_count']} items)")
        passed += 1

        near_results = engine.find_near("e1", {"role": "button"})
        assert len(near_results) > 0
        print(f"  ✓ find_near: e1 附近找到 {len(near_results)} 个 button")
        passed += 1

        verify = engine.verify_ref("e2", "click")
        assert verify["valid"]
        print(f"  ✓ verify_ref: e2 click → valid")
        passed += 1

        verify_invalid = engine.verify_ref("r4", "click")
        assert not verify_invalid["valid"], "container should not be clickable"
        print(f"  ✓ verify_ref: r4 click → invalid (正确：容器不可点击)")
        passed += 1

    except Exception as e:
        print(f"  ✗ FAIL: {e}")
        failed += 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    # ═══ 阶段4: TargetSelector ═══
    print("\n" + "=" * 60)
    print("阶段4: TargetSelector - 目标选择测试")
    print("=" * 60)

    temp_dir = tempfile.mkdtemp()
    store = SnapshotStore(session="functional_test", base_dir=temp_dir)
    try:
        ref = store.save_full(SAMPLE_SNAPSHOT)
        sid = ref["snapshot_id"]
        indexer = SnapshotIndexer()
        index_data = indexer.build_index(SAMPLE_SNAPSHOT)
        store.save_index(sid, index_data)
        all_nodes = (
            list(SAMPLE_SNAPSHOT["data"]["index"]["interactable_elements"])
            + list(SAMPLE_SNAPSHOT["data"]["index"]["surface_index"])
            + list(SAMPLE_SNAPSHOT["data"]["index"]["deep_index"])
        )
        compressed = indexer.build_compressed_index(all_nodes)
        store.save_compressed_index(sid, {"groups": compressed})

        selector = TargetSelector(store=store)
        selector._engine.load(sid)

        cases = [
            {
                "name": "搜索按钮",
                "query": {"intent": "click", "target_hint": "搜索按钮", "target_constraints": {"role": ["button"], "text_or_name": ["搜索"]}},
                "expected_ref": None, "expected_status": "selected",
            },
            {
                "name": "搜索输入框",
                "query": {"intent": "type", "target_hint": "搜索输入框", "target_constraints": {"role": ["searchbox", "textbox"]}},
                "expected_ref": None, "expected_status": "selected",
            },
            {
                "name": "下一页按钮",
                "query": {"intent": "click", "target_hint": "下一页", "target_constraints": {"role": ["link", "button"], "text_or_name": ["下一页", "next"]}},
                "expected_ref": None, "expected_status": "selected",
            },
            {
                "name": "不存在的按钮",
                "query": {"intent": "click", "target_hint": "不存在的按钮", "target_constraints": {}},
                "expected_ref": None, "expected_status": "not_found",
            },
            {
                "name": "列表项目(多候选)",
                "query": {"intent": "click", "target_hint": "Item", "target_constraints": {"role": ["link"]}},
                "expected_ref": None, "expected_status": ["need_approval", "selected"],
            },
        ]

        for case in cases:
            result = selector.select(case["query"])
            expected_status = case["expected_status"]
            if isinstance(expected_status, list):
                assert result["status"] in expected_status, f"{case['name']}: expected status in {expected_status}, got {result['status']}"
            else:
                assert result["status"] == expected_status, f"{case['name']}: expected {expected_status}, got {result['status']}"
            print(f"  ✓ {case['name']}: {result['status']} (ref={result.get('target_ref', 'N/A')})")
            passed += 1

    except Exception as e:
        print(f"  ✗ FAIL: {e}")
        failed += 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    # ═══ 阶段5: 结构压缩 ═══
    print("\n" + "=" * 60)
    print("阶段5: 结构压缩 - 相似元素分组测试")
    print("=" * 60)

    try:
        indexer = SnapshotIndexer()
        similar_items = [
            {"ref": f"e{10+i}", "tag": "a", "role": "link", "ref_type": "element", "parent_ref": "r_list", "text": f"Item {i}", "href": f"/items/{i}"}
            for i in range(1, 51)
        ]
        compressed = indexer.build_compressed_index(similar_items, min_group_size=3)
        assert len(compressed) > 0, "should create at least 1 compressed group"
        group = compressed[0]
        assert group["count"] == 50, f"should compress 50 items, got {group['count']}"
        print(f"  ✓ 压缩 50 个相似元素: {len(compressed)} 组 (每组 {group['count']} 个)")
        passed += 1

        assert len(group["data"]["_ref"]) == 50, "should preserve all refs"
        print(f"  ✓ ref 可追溯: {len(group['data']['_ref'])} refs 保留")
        passed += 1

        assert len(group["samples"]) > 0, "should have samples"
        print(f"  ✓ samples: {len(group['samples'])} 个样本")
        passed += 1

    except Exception as e:
        print(f"  ✗ FAIL: {e}")
        failed += 1

    # ═══ 总结 ═══
    print("\n" + "=" * 60)
    print(f"功能测试完成: {passed} 通过, {failed} 失败")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = test_full_pipeline()
    sys.exit(0 if success else 1)
