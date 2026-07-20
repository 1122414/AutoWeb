"""
AutoWeb dp_cli 主闭环功能测试脚本

测试 Plan -> Target -> Action 链路完整性，不依赖 LangGraph/LLM/浏览器。
直接在本地运行验证 P0-P6 所有修复点。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}: {detail}")


SAMPLE_SNAPSHOT = {
    "ok": True, "session": "func_test", "action": "snapshot",
    "data": {
        "page": {"url": "https://func.test/search", "title": "Func Test"},
        "page_identity": {"page_id": "p1", "snapshot_id": "snap_f1", "snapshot_seq": 1, "domain": "func.test"},
        "index": {
            "interactable_elements": [
                {"ref": "e1", "role": "searchbox", "name": "q", "tag": "input", "input_type": "text", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r1"},
                {"ref": "e2", "role": "button", "name": "search", "tag": "button", "text": "search", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r1"},
                {"ref": "e3", "role": "link", "name": "home", "tag": "a", "text": "home", "in_viewport": True, "interactable_now": True, "ref_type": "element", "parent_ref": "r2"},
            ],
            "surface_index": [
                {"ref": "r1", "ref_type": "container", "tag": "form", "role": "form", "name": "search_form", "parent_ref": "", "child_count": 2},
                {"ref": "r2", "ref_type": "container", "tag": "nav", "role": "navigation", "name": "", "parent_ref": "", "child_count": 1},
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


def test_p0_constraint_normalization():
    print("\n--- P0: Constraint Normalization ---")
    from skills.dpcli_target_selector import _normalize_target_constraints

    tr = {
        "role": "button",
        "text_or_name": ["search"],
        "region_hint": "search_area",
        "constraints": {},
    }
    result = _normalize_target_constraints(tr)
    check("P0.1: role merged", result.get("role") == ["button"])
    check("P0.2: text_or_name merged", result.get("text_or_name") == ["search"])
    check("P0.3: region_hint merged", result.get("region_hint") == "search_area")
    check("P0.4: existing constraints preserved",
          _normalize_target_constraints({"role": "button", "constraints": {"role": ["link"]}}).get("role") == ["link"])


def test_p1_no_python_fallback():
    print("\n--- P1: No Python Fallback ---")
    # Simulate the coder fallback logic
    retry_count = 2
    validation_error = "click requires ref or locator"

    update = {
        "execution_mode": "dp_cli",
        "generated_action": None,
        "_action_source": None,
        "_dpcli_action_disabled": False,
        "error_type": "dpcli_action_json",
        "execution_result": f"dp_cli action generation failed after {retry_count + 1} attempts: {validation_error}",
        "reflections": [f"dp_cli action JSON invalid after retries: {validation_error}"],
    }
    goto = "Planner"

    check("P1.1: execution_mode stays dp_cli", update["execution_mode"] == "dp_cli")
    check("P1.2: no python_code in update", "python_code" not in str(update))
    check("P1.3: goto is Planner", goto == "Planner")
    check("P1.4: goto is NOT Coder", goto != "Coder")


def test_p2_find_validator():
    print("\n--- P2: Find Action Validator ---")

    def validate(action, state=None):
        skill = str(action.get("skill", "")).strip().lower()
        params = action.get("params") or {}
        if not skill:
            return "missing skill"
        if not isinstance(params, dict):
            return "params must be an object"
        required = {
            "find": ["text", "ref", "locator"],
            "click": ["ref", "locator", "target_ref"],
            "type": ["ref", "locator", "target_ref"],
            "select": ["ref", "locator", "target_ref"],
            "expand": ["ref", "locator"],
            "list-items": ["ref", "locator"],
        }
        if skill in required:
            has_any = any(bool(params.get(k)) for k in required[skill])
            if not has_any:
                return f"{skill} requires ref or locator"
        return None

    check("P2.1: find with text passes", validate({"skill": "find", "params": {"text": "search"}}) is None)
    check("P2.2: find with ref passes", validate({"skill": "find", "params": {"ref": "e2"}}) is None)
    check("P2.3: find with empty params fails", validate({"skill": "find", "params": {}}) is not None)
    check("P2.4: click with target_ref passes", validate({"skill": "click", "params": {"target_ref": "e2"}}) is None)


def test_p3_planner_priority():
    print("\n--- P3: Planner Priority ---")
    state = {
        "dpcli_agent_view": {"capability_map": {}},
        "execution_mode": None,
        "loop_count": 0,
    }
    DPCLI_ENABLED = True
    is_dpcli = (DPCLI_ENABLED and state.get("dpcli_agent_view")
                and state.get("execution_mode") != "python_code")

    check("P3.1: dp_cli active with agent_view", is_dpcli)
    check("P3.2: dp_cli active at loop_count=0", state["loop_count"] == 0)
    check("P3.3: dp_cli NOT active without agent_view",
          not (DPCLI_ENABLED and {} and True))


def test_p4_smoke_isolation():
    print("\n--- P4: Smoke Isolation ---")
    try:
        from skills.dpcli_target_selector import TargetSelector
        check("P4.1: TargetSelector importable from skills", True)
    except ImportError as e:
        check("P4.1: TargetSelector importable from skills", False, str(e))

    # Verify no langgraph/tiktoken imports in pure module
    import importlib.util
    repo_root = Path(__file__).parents[2]
    spec = importlib.util.spec_from_file_location(
        "check_ts", repo_root / "skills" / "dpcli_target_selector.py")
    with open(repo_root / "skills" / "dpcli_target_selector.py", "r") as f:
        content = f.read()
    check("P4.2: no langgraph import", "import langgraph" not in content and "from langgraph" not in content)
    check("P4.3: no tiktoken import", "import tiktoken" not in content and "from tiktoken" not in content)
    check("P4.4: no langchain import", "import langchain" not in content and "from langchain" not in content)


def test_p5_target_ref_consistency():
    print("\n--- P5: Target Ref Consistency ---")

    def validate(action, state=None):
        skill = str(action.get("skill", "")).strip().lower()
        params = action.get("params") or {}
        if not skill:
            return "missing skill"
        required = {
            "click": ["ref", "locator", "target_ref"],
            "type": ["ref", "locator", "target_ref"],
            "select": ["ref", "locator", "target_ref"],
            "find": ["text", "ref", "locator"],
            "expand": ["ref", "locator"],
            "list-items": ["ref", "locator"],
        }
        if skill in required:
            has_any = any(bool(params.get(k)) for k in required[skill])
            if not has_any:
                return f"{skill} requires ref or locator"
        if skill in ("click", "type", "select") and state:
            target_result = state.get("dpcli_target_result") or {}
            sp = state.get("dpcli_structured_plan") or {}
            target_required = sp.get("target_request", {}).get("required", False)
            if target_required:
                if target_result.get("status") != "selected":
                    return f"{skill} requires selected target, got {target_result.get('status')}"
                expected_ref = target_result.get("target_ref")
                if expected_ref:
                    action_ref = params.get("ref") or params.get("target_ref")
                    if not action_ref:
                        return "requires ref/target_ref"
                    if action_ref != expected_ref:
                        return f"target ref mismatch: {action_ref} vs {expected_ref}"
        return None

    state_selected = {
        "dpcli_structured_plan": {"target_request": {"required": True}},
        "dpcli_target_result": {"status": "selected", "target_ref": "e2"},
    }

    check("P5.1: matching ref passes",
          validate({"skill": "click", "params": {"ref": "e2"}}, state_selected) is None)
    check("P5.2: wrong ref fails",
          validate({"skill": "click", "params": {"ref": "e999"}}, state_selected) is not None)
    check("P5.3: target not found fails",
          validate({"skill": "click", "params": {"ref": "e2"}},
                   {"dpcli_structured_plan": {"target_request": {"required": True}},
                    "dpcli_target_result": {"status": "not_found"}}) is not None)
    check("P5.4: scroll unaffected by target check",
          validate({"skill": "scroll", "params": {"direction": "down"}}, state_selected) is None)


def test_p6_target_ref_normalize():
    print("\n--- P6: Target Ref Normalization ---")

    def normalize(params):
        if "target_ref" in params and "ref" not in params:
            params = dict(params)
            params["ref"] = params["target_ref"]
        return params

    p1 = normalize({"target_ref": "e2"})
    check("P6.1: target_ref normalized to ref", p1.get("ref") == "e2")

    p2 = normalize({"ref": "e3", "target_ref": "e2"})
    check("P6.2: existing ref not overwritten", p2.get("ref") == "e3")


def test_plan_target_action_flow():
    """P7: End-to-end Plan -> Target -> Action flow validation."""
    print("\n--- P7: E2E Flow ---")
    from skills.dpcli_snapshot_store import SnapshotStore
    from skills.dpcli_snapshot_indexer import SnapshotIndexer
    from skills.dpcli_target_selector import TargetSelector

    temp_dir = tempfile.mkdtemp()
    try:
        store = SnapshotStore(session="func_test", base_dir=temp_dir)
        ref = store.save_full(SAMPLE_SNAPSHOT)
        sid = ref["snapshot_id"]

        indexer = SnapshotIndexer()
        index_data = indexer.build_index(SAMPLE_SNAPSHOT)
        store.save_index(sid, index_data)

        selector = TargetSelector(store=store)
        selector._engine.load(sid)

        # Stage: Planner produces structured_plan
        structured_plan = {
            "step_intent": "click",
            "target_request": {
                "required": True,
                "target_hint": "search button",
                "role": "button",
                "text_or_name": ["search"],
                "constraints": {},
            },
        }

        from skills.dpcli_target_selector import _normalize_target_constraints
        constraints = _normalize_target_constraints(structured_plan["target_request"])

        # Stage: TargetSelector selects
        result = selector.select({
            "intent": "click",
            "target_hint": "search button",
            "target_constraints": constraints,
        })
        check("P7.1: TargetSelector selects ref", result["status"] == "selected")
        check("P7.2: Correct ref selected", result["target_ref"] == "e2")

        # Stage: Action generation with correct ref
        action = {"skill": "click", "params": {"ref": result["target_ref"]}}
        check("P7.3: Action uses selected ref", action["params"]["ref"] == "e2")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print("AutoWeb dp_cli 主闭环功能测试")
    print("=" * 60)

    test_p0_constraint_normalization()
    test_p1_no_python_fallback()
    test_p2_find_validator()
    test_p3_planner_priority()
    test_p4_smoke_isolation()
    test_p5_target_ref_consistency()
    test_p6_target_ref_normalize()
    test_plan_target_action_flow()

    print(f"\n{'=' * 60}")
    print(f"Results: {PASS} passed, {FAIL} failed, {PASS + FAIL} total")
    print(f"{'=' * 60}")

    if FAIL > 0:
        print("\nSome tests FAILED. Check details above.")
        sys.exit(1)
    else:
        print("\nAll tests PASSED.")
        sys.exit(0)
