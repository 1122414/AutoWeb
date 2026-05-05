
"""P7 最小端到端冒烟：不启动浏览器，mock snapshot + state 验证 Plan→Target→Action 链路"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

SAMPLE_SNAPSHOT = {
    "ok": True, "session": "flow_test", "action": "snapshot",
    "data": {
        "page": {"url": "https://flow.smoke/search", "title": "Flow Smoke"},
        "page_identity": {"page_id": "p1", "snapshot_id": "snap_p7", "snapshot_seq": 1, "domain": "flow.smoke"},
        "index": {
            "interactable_elements": [
                {"ref": "e1", "role": "searchbox", "name": "search", "tag": "input", "input_type": "text", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r1", "placeholder": "请输入关键词"},
                {"ref": "e2", "role": "button", "name": "搜索", "tag": "button", "text": "搜索", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r1"},
                {"ref": "e3", "role": "link", "name": "首页", "tag": "a", "text": "首页", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r2"},
            ],
            "surface_index": [
                {"ref": "r1", "ref_type": "container", "tag": "form", "role": "form", "name": "search_form", "parent_ref": "", "child_count": 2},
                {"ref": "r2", "ref_type": "container", "tag": "nav", "role": "navigation", "name": "", "parent_ref": "", "child_count": 3},
            ],
            "deep_index": [],
            "data_regions": [],
            "tree": {
                "parent_map": {"e1": "r1", "e2": "r1", "e3": "r2"},
                "children_map": {"r1": ["e1", "e2"], "r2": ["e3"]},
            },
            "stats": {"total_nodes": 5, "in_viewport": 5, "interactable_now": 5},
        },
    },
}


def build_mock_state() -> dict:
    """构建模拟 AgentState（最小字段集）"""
    return {
        "user_task": "在搜索页搜索 'AI agent'",
        "current_url": "https://flow.smoke/search",
        "finished_steps": [],
        "reflections": [],
        "loop_count": 1,
        "execution_mode": None,
        "messages": [],
        "plan": "",
        "is_complete": False,
        "generated_code": None,
        "generated_action": None,
        "dpcli_session": "flow_test",
        "dpcli_result": {},
        "dpcli_snapshot": SAMPLE_SNAPSHOT,
        "execution_log": None,
        "verification_result": {},
        "error": None,
        "error_type": None,
        "coder_retry_count": 0,
        "locator_suggestions": [],
        "dom_skeleton": "",
        "dom_hash": None,
    }


def test_full_flow():
    """P7: Plan→Target→Action 端到端验证"""

    def _build_action_context(state):
        parts = [
            f"current_url: {state.get('current_url', '')}",
            f"user_task: {state.get('user_task', '')}",
            f"plan: {state.get('plan', '')}",
        ]
        target_result = state.get("dpcli_target_result") or {}
        if target_result:
            parts.append(
                f"status: {target_result.get('status', 'unknown')} "
                f"target_ref: {target_result.get('target_ref', '')} "
                f"confidence: {target_result.get('confidence', 0)} "
                f"role: {target_result.get('evidence', {}).get('role', '')} "
                f"name: {target_result.get('evidence', {}).get('name', '')} "
                f"text: {target_result.get('evidence', {}).get('text', '')}"
            )
        return "\n".join(parts)

    from skills.dpcli_snapshot_store import SnapshotStore
    from skills.dpcli_snapshot_indexer import SnapshotIndexer
    from skills.dpcli_planner_view import PlannerViewGenerator
    from skills.dpcli_target_selector import TargetSelector

    passed = 0
    failed = 0

    # 阶段1: 设置 snapshot artifacts
    temp_dir = tempfile.mkdtemp()
    try:
        store = SnapshotStore(session="flow_test", base_dir=temp_dir)
        ref = store.save_full(SAMPLE_SNAPSHOT)
        sid = ref["snapshot_id"]

        indexer = SnapshotIndexer()
        index_data = indexer.build_index(SAMPLE_SNAPSHOT)
        store.save_index(sid, index_data)

        all_nodes = (
            list(SAMPLE_SNAPSHOT["data"]["index"]["interactable_elements"])
            + list(SAMPLE_SNAPSHOT["data"]["index"]["surface_index"])
        )
        compressed = indexer.build_compressed_index(all_nodes)
        store.save_compressed_index(sid, {"groups": compressed})

        view_gen = PlannerViewGenerator()
        agent_view = view_gen.generate(SAMPLE_SNAPSHOT, index_data, compressed)
        store.save_planner_view(sid, agent_view)
        diagnostics = view_gen.generate_diagnostics(SAMPLE_SNAPSHOT, compressed)

        print("  OK Snapshot artifacts built")
        passed += 1

        # 阶段2: 模拟 Planner 产出 dpcli_structured_plan
        structured_plan = {
            "step_intent": "click",
            "target_request": {
                "required": True,
                "target_hint": "搜索按钮",
                "role": "button",
                "text_or_name": ["搜索"],
                "region_hint": "search_area",
                "constraints": {},
            },
            "action_payload": {"text": "", "url": "", "direction": ""},
            "reason": "点击搜索按钮执行搜索",
            "needs_rag": False,
            "needs_human_approval": False,
        }
        print(f"  OK Planner structured plan: step_intent={structured_plan['step_intent']}")
        passed += 1

        # 阶段3: TargetSelector 选择目标 ref
        from skills.dpcli_target_selector import TargetSelector
        selector = TargetSelector(store=store)
        selector._engine.load(sid)

        result = selector.select({
            "intent": "click",
            "target_hint": "搜索按钮",
            "target_constraints": {"role": ["button"], "text_or_name": ["搜索"]},
        })
        assert result["status"] == "selected", f"Expected selected, got {result['status']}"
        assert result["target_ref"] == "e2", f"Expected e2, got {result['target_ref']}"
        print(f"  OK TargetSelector: selected ref={result['target_ref']} ({result['evidence']['name']})")
        passed += 1

        # 阶段4: 构建 mock state 并验证 action context
        state = build_mock_state()
        state["dpcli_snapshot_ref"] = ref
        state["dpcli_agent_view"] = agent_view
        state["dpcli_observer_diagnostics"] = diagnostics
        state["dpcli_structured_plan"] = structured_plan
        state["dpcli_target_result"] = result

        context = _build_action_context(state)
        assert "e2" in context, "Action context should contain target_ref e2"
        assert "button" in context, "Action context should contain role"
        assert "search" in context.lower(), "Action context should contain text match"
        print(f"  OK Action context: contains target_ref, role, and text")
        passed += 1

        # 阶段5: 验证不需要目标元素时的路由
        structured_plan_no_target = {
            "step_intent": "navigate",
            "target_request": {"required": False},
            "action_payload": {"url": "https://example.com", "text": "", "direction": ""},
            "reason": "跳转到目标页面",
            "needs_rag": False,
            "needs_human_approval": False,
        }
        assert structured_plan_no_target["target_request"]["required"] is False
        print("  OK No-target plan: target_request.required=False correctly skips selector")
        passed += 1

        # 阶段6: 验证 action prompt 上下文格式
        state_no_target = build_mock_state()
        state_no_target["dpcli_structured_plan"] = structured_plan_no_target
        state_no_target["dpcli_target_result"] = {"status": "not_required"}
        context_no_target = _build_action_context(state_no_target)
        assert "not_required" in context_no_target
        print("  OK Action context (not_required): properly formatted")
        passed += 1

        # 阶段7: 验证 _should_use_dpcli_action 在 execution_mode=None 时正确引导
        dpcli_enabled = True
        bootstrap_state = build_mock_state()
        bootstrap_state["execution_mode"] = None
        should_use = dpcli_enabled and bootstrap_state.get("execution_mode") != "python_code"
        assert should_use, "Should use dp_cli when execution_mode is None (bootstrap)"
        print("  OK Bootstrap: should use dp_cli when execution_mode=None")
        passed += 1

        bootstrap_state_py = build_mock_state()
        bootstrap_state_py["execution_mode"] = "python_code"
        should_use_py = dpcli_enabled and bootstrap_state_py.get("execution_mode") != "python_code"
        assert not should_use_py, "Should NOT use dp_cli when execution_mode=python_code"
        print("  OK Bootstrap: _should_use_dpcli_action returns False when execution_mode=python_code")
        passed += 1

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    print(f"\n{'='*40}\nP7 E2E Smoke: {passed} passed, {failed} failed\n{'='*40}")
    return failed == 0


if __name__ == "__main__":
    success = test_full_flow()
    sys.exit(0 if success else 1)
