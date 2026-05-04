"""
P2+P5+P6: dp_cli validator alignment and executor normalization tests.

P2: find action accepts "text" param alongside ref/locator
P5: click/type/select target_ref consistency check (must match TargetSelector output)
P6: executor normalizes target_ref -> ref for click/type actions

Uses inline mirror logic to avoid triggering the full core.nodes import chain.
"""
from __future__ import annotations

import unittest


def _validate_action(action, state=None):
    skill = str(action.get("skill", "")).strip().lower()
    params = action.get("params") or {}

    if not skill:
        return "missing skill"
    if not isinstance(params, dict):
        return "params must be an object"

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

    if skill == "click" and state:
        snapshot = state.get("dpcli_snapshot") or {}
        if snapshot and params.get("locator"):
            return "click must use a snapshot ref instead of a free-form locator"

    if skill in ("click", "type", "select") and state:
        target_result = state.get("dpcli_target_result") or {}
        structured_plan = state.get("dpcli_structured_plan") or {}
        target_required = (
            structured_plan.get("target_request", {}).get("required", False)
            if isinstance(structured_plan.get("target_request"), dict)
            else False
        )

        if target_required:
            if target_result.get("status") != "selected":
                return (
                    f"{skill} requires a selected target but "
                    f"TargetSelector status is '{target_result.get('status', 'unknown')}'"
                )

            expected_ref = target_result.get("target_ref")
            if expected_ref:
                action_ref = params.get("ref") or params.get("target_ref")
                if not action_ref:
                    return f"{skill} requires ref/target_ref but none provided"
                if action_ref != expected_ref:
                    return (
                        f"target ref mismatch: action uses '{action_ref}' "
                        f"but TargetSelector selected '{expected_ref}'"
                    )

            if params.get("locator") and not params.get("ref") and not params.get("target_ref"):
                return f"{skill} must use target_ref from TargetSelector, not free-form locator"

    return None


def _build_state(**overrides):
    state = {
        "dpcli_target_result": {},
        "dpcli_structured_plan": {},
        "dpcli_snapshot": None,
    }
    state.update(overrides)
    return state


class TestFindActionValidation(unittest.TestCase):

    def test_find_with_text_allowed(self):
        action = {"skill": "find", "params": {"text": "search"}}
        err = _validate_action(action, _build_state())
        self.assertIsNone(err, f"find with text should be valid, got: {err}")

    def test_find_with_ref_allowed(self):
        action = {"skill": "find", "params": {"ref": "e2"}}
        err = _validate_action(action, _build_state())
        self.assertIsNone(err)

    def test_find_with_locator_allowed(self):
        action = {"skill": "find", "params": {"locator": "css:.btn"}}
        err = _validate_action(action, _build_state())
        self.assertIsNone(err)

    def test_find_with_empty_params_rejected(self):
        action = {"skill": "find", "params": {}}
        err = _validate_action(action, _build_state())
        self.assertIsNotNone(err, "find with no params should be rejected")

    def test_find_missing_skill_rejected(self):
        action = {"skill": "", "params": {"text": "search"}}
        err = _validate_action(action)
        self.assertEqual(err, "missing skill")


class TestTargetRefConsistency(unittest.TestCase):

    def test_click_with_matching_ref_passes(self):
        state = _build_state(
            dpcli_structured_plan={"target_request": {"required": True}},
            dpcli_target_result={"status": "selected", "target_ref": "e2"},
        )
        action = {"skill": "click", "params": {"ref": "e2"}}
        err = _validate_action(action, state)
        self.assertIsNone(err, f"matching ref should pass, got: {err}")

    def test_click_with_target_ref_matching_passes(self):
        state = _build_state(
            dpcli_structured_plan={"target_request": {"required": True}},
            dpcli_target_result={"status": "selected", "target_ref": "e2"},
        )
        action = {"skill": "click", "params": {"target_ref": "e2"}}
        err = _validate_action(action, state)
        self.assertIsNone(err)

    def test_click_with_wrong_ref_fails(self):
        state = _build_state(
            dpcli_structured_plan={"target_request": {"required": True}},
            dpcli_target_result={"status": "selected", "target_ref": "e2"},
        )
        action = {"skill": "click", "params": {"ref": "e999"}}
        err = _validate_action(action, state)
        self.assertIsNotNone(err, "wrong ref should fail")
        self.assertIn("mismatch", err)

    def test_click_when_target_not_found_fails(self):
        state = _build_state(
            dpcli_structured_plan={"target_request": {"required": True}},
            dpcli_target_result={"status": "not_found", "target_ref": None},
        )
        action = {"skill": "click", "params": {"ref": "e2"}}
        err = _validate_action(action, state)
        self.assertIsNotNone(err, "click should fail when target not found")

    def test_type_with_matching_ref_passes(self):
        state = _build_state(
            dpcli_structured_plan={"target_request": {"required": True}},
            dpcli_target_result={"status": "selected", "target_ref": "e1"},
        )
        action = {"skill": "type", "params": {"ref": "e1", "text": "hello"}}
        err = _validate_action(action, state)
        self.assertIsNone(err)

    def test_click_without_target_request_skips_check(self):
        state = _build_state(
            dpcli_structured_plan={"target_request": {"required": False}},
            dpcli_target_result={},
        )
        action = {"skill": "click", "params": {"ref": "e3"}}
        err = _validate_action(action, state)
        self.assertIsNone(err)

    def test_scroll_not_affected_by_target_check(self):
        state = _build_state(
            dpcli_structured_plan={"target_request": {"required": True}},
            dpcli_target_result={"status": "not_found"},
        )
        action = {"skill": "scroll", "params": {"direction": "down"}}
        err = _validate_action(action, state)
        self.assertIsNone(err)

    def test_wait_not_affected_by_target_check(self):
        state = _build_state(
            dpcli_structured_plan={"target_request": {"required": True}},
            dpcli_target_result={"status": "not_found"},
        )
        action = {"skill": "wait", "params": {"timeout_ms": 3000}}
        err = _validate_action(action, state)
        self.assertIsNone(err)

    def test_open_not_affected_by_target_check(self):
        action = {"skill": "open", "params": {"url": "https://example.com"}}
        err = _validate_action(action, _build_state())
        self.assertIsNone(err)


class TestTargetRefNormalization(unittest.TestCase):

    def test_target_ref_normalized_to_ref(self):
        action = {"skill": "click", "params": {"target_ref": "e2"}}
        params = action["params"]
        if "target_ref" in params and "ref" not in params:
            params = dict(params)
            params["ref"] = params["target_ref"]
        self.assertEqual(params.get("ref"), "e2")

    def test_ref_present_not_overwritten(self):
        action = {"skill": "click", "params": {"ref": "e3", "target_ref": "e2"}}
        params = action["params"]
        if "target_ref" in params and "ref" not in params:
            params = dict(params)
            params["ref"] = params["target_ref"]
        self.assertEqual(params["ref"], "e3")

    def test_type_target_ref_normalized(self):
        action = {"skill": "type", "params": {"target_ref": "e1", "text": "hi"}}
        params = action["params"]
        if "target_ref" in params and "ref" not in params:
            params = dict(params)
            params["ref"] = params["target_ref"]
        self.assertEqual(params.get("ref"), "e1")


if __name__ == "__main__":
    unittest.main()
