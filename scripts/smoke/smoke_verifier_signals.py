#!/usr/bin/env python
"""Verifier Deterministic Signal Enhancement — Functional Smoke Test.

Exercises the full verifier pipeline with mock state objects.
Does NOT require browser, Milvus, or external services.

Run: python scripts/smoke/smoke_verifier_signals.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Install lightweight stubs before importing core.nodes
import tests.unit.stubs  # noqa: F401


def green(text: str) -> str:
    return f"\033[92m{text}\033[0m"


def red(text: str) -> str:
    return f"\033[91m{text}\033[0m"


def yellow(text: str) -> str:
    return f"\033[93m{text}\033[0m"


def assert_equals(actual, expected, label: str) -> bool:
    ok = actual == expected
    if ok:
        print(f"  {green('[PASS]')} {label}")
    else:
        print(f"  {red('[FAIL]')} {label}: expected={expected!r}, got={actual!r}")
    return ok


def assert_not_none(value, label: str) -> bool:
    ok = value is not None
    if ok:
        print(f"  {green('[PASS]')} {label}")
    else:
        print(f"  {red('[FAIL]')} {label}: got None")
    return ok


def assert_is_none(value, label: str) -> bool:
    ok = value is None
    if ok:
        print(f"  {green('[PASS]')} {label}")
    else:
        print(f"  {red('[FAIL]')} {label}: expected None, got {value!r}")
    return ok


def run_tests() -> int:
    failures = 0

    # ---- Import core functions ----
    from core.nodes._verification import (
        _build_verification_result,
        _coerce_verification_result,
        _parse_verifier_result_content,
    )
    from core.nodes.verifier import (
        _verify_dpcli_action_with_signals,
        _route_by_error_type,
        _detect_duplicate_action,
    )
    from core.nodes._dpcli import _dpcli_action_kind

    # ============================
    # 1. _build_verification_result new fields
    # ============================
    print(f"\n{yellow('=== 1. _build_verification_result — New Fields ===')}")
    vr = _build_verification_result(is_success=True, summary="ok", source="verifier")
    failures += not assert_equals(vr["confidence"], 1.0, "success → confidence=1.0")
    failures += not assert_equals(vr["decision_source"], "", "default decision_source=''")
    failures += not assert_equals(vr["needs_llm"], False, "default needs_llm=False")
    failures += not assert_equals(vr["warnings"], [], "default warnings=[]")

    vr2 = _build_verification_result(is_success=False, summary="fail", source="verifier")
    failures += not assert_equals(vr2["confidence"], 0.0, "failure → confidence=0.0")

    vr3 = _build_verification_result(
        is_success=True, summary="ok", source="verifier",
        confidence=0.85, decision_source="url_match",
        needs_llm=True, warnings=["w1"],
    )
    failures += not assert_equals(vr3["confidence"], 0.85, "explicit confidence")
    failures += not assert_equals(vr3["decision_source"], "url_match", "explicit decision_source")
    failures += not assert_equals(vr3["needs_llm"], True, "explicit needs_llm")

    # 1b. _coerce passes through new fields
    coerced = _coerce_verification_result(
        {"confidence": 0.75, "decision_source": "error_type", "needs_llm": True, "warnings": ["a"]},
        fallback_is_success=False)
    failures += not assert_equals(coerced["confidence"], 0.75, "coerce passes confidence")
    failures += not assert_equals(coerced["decision_source"], "error_type", "coerce passes decision_source")

    # ============================
    # 2. Parser robustness
    # ============================
    print(f"\n{yellow('=== 2. Parser Robustness ===')}")
    parsed = _parse_verifier_result_content("Status : STEP_SUCCESS\nSummary : done")
    failures += not assert_equals(parsed["is_success"], True, "space before colon → success")

    parsed2 = _parse_verifier_result_content("status: step_fail\nSummary: err")
    failures += not assert_equals(parsed2["is_success"], False, "lowercase → fail")

    parsed3 = _parse_verifier_result_content("Status:STEP_SUCCESS\nSummary:ok")
    failures += not assert_equals(parsed3["is_success"], True, "no space after colon → success")

    # ============================
    # 3. error_type routing
    # ============================
    print(f"\n{yellow('=== 3. Error Type Routing ===')}")
    state = {"error_type": "dpcli_ref_stale", "plan": "click", "locator_suggestions": []}
    cmd = _route_by_error_type(state, "click", "llm")
    failures += not assert_not_none(cmd, "ref_stale → returns Command")
    if cmd:
        failures += not assert_equals(cmd.goto, "Observer", "ref_stale → Observer")
        failures += not assert_equals(
            cmd.update["verification_result"]["decision_source"], "error_type",
            "ref_stale → decision_source=error_type")

    state2 = {"error_type": "critical", "plan": "x", "locator_suggestions": []}
    cmd2 = _route_by_error_type(state2, "x", "llm")
    failures += not assert_not_none(cmd2, "critical → returns Command")
    if cmd2:
        failures += not assert_equals(cmd2.goto, "Planner", "critical → Planner")
        failures += not assert_equals(
            cmd2.update["verification_result"]["failure_scope"], "global",
            "critical → failure_scope=global")

    state3 = {"error_type": None, "plan": "x", "locator_suggestions": []}
    cmd3 = _route_by_error_type(state3, "x", "llm")
    failures += not assert_is_none(cmd3, "None error_type → fall through")

    # ============================
    # 4. dp_cli URL matching
    # ============================
    print(f"\n{yellow('=== 4. dp_cli URL Matching ===')}")
    nav_state = {
        "generated_action": {"skill": "open", "params": {"url": "https://example.com/page"}},
        "dpcli_result": {"ok": True, "data": {"page": {"url": "https://example.com/page"}}},
        "dpcli_execution_evidence": {
            "before_url": "", "after_url": "https://example.com/page", "url_changed": True},
        "dpcli_structured_plan": {
            "step_intent": "navigate", "action_payload": {"url": "https://example.com/page"}},
    }
    r = _verify_dpcli_action_with_signals(nav_state, "https://example.com/page")
    failures += not assert_not_none(r, "navigate exact match → success")
    if r:
        failures += not assert_equals(r["is_success"], True, "navigate is_success")
        failures += not assert_equals(r["decision_source"], "url_match", "navigate decision_source")

    # click without URL → defer
    click_state = {
        "generated_action": {"skill": "click", "params": {"ref": "e1"}},
        "dpcli_result": {"ok": True, "data": {"page": {"url": "https://example.com/same"}}},
        "dpcli_execution_evidence": {
            "before_url": "https://example.com/same", "after_url": "https://example.com/same", "url_changed": False},
        "dpcli_structured_plan": {"step_intent": "click", "action_payload": {}},
    }
    r2 = _verify_dpcli_action_with_signals(click_state, "https://example.com/same")
    failures += not assert_is_none(r2, "click no URL no change → defer to LLM")

    # ============================
    # 5. Data schema validation
    # ============================
    print(f"\n{yellow('=== 5. Data Schema Validation ===')}")
    schema_state = {
        "generated_action": {"skill": "extract", "params": {"schema": ["title", "url", "price"]}},
        "dpcli_result": {"ok": True, "data": {"items": [
            {"title": "A", "url": "https://a", "price": 10},
            {"title": "B", "url": "https://b"},
        ]}},
        "dpcli_structured_plan": {"step_intent": "extract", "action_payload": {"schema": ["title", "url", "price"]}},
    }
    r3 = _verify_dpcli_action_with_signals(schema_state, "")
    failures += not assert_not_none(r3, "schema sufficient → success")
    if r3:
        failures += not assert_equals(r3["decision_source"], "schema_match", "schema → decision_source")

    # insufficient schema coverage
    poor_schema_state = {
        "generated_action": {"skill": "extract", "params": {"schema": ["a", "b", "c", "d", "e"]}},
        "dpcli_result": {"ok": True, "data": {"items": [{"a": 1}]}},
        "dpcli_structured_plan": {"step_intent": "extract", "action_payload": {"schema": ["a", "b", "c", "d", "e"]}},
    }
    r4 = _verify_dpcli_action_with_signals(poor_schema_state, "")
    failures += not assert_is_none(r4, "schema insufficient → defer to LLM")

    # ============================
    # 6. Scroll/wait tentative success
    # ============================
    print(f"\n{yellow('=== 6. Scroll/Wait Tentative Success ===')}")
    scroll_state = {
        "generated_action": {"skill": "scroll", "params": {"direction": "down"}},
        "dpcli_result": {"ok": True, "data": {"page": {"url": "https://x.com"}}},
        "dpcli_execution_evidence": {
            "before_url": "https://x.com", "after_url": "https://x.com", "url_changed": False},
        "dpcli_structured_plan": {"step_intent": "scroll", "action_payload": {}},
    }
    r5 = _verify_dpcli_action_with_signals(scroll_state, "https://x.com")
    failures += not assert_not_none(r5, "scroll → tentative success")
    if r5:
        failures += not assert_equals(r5["is_success"], True, "scroll is_success")
        failures += not assert_equals(r5["needs_llm"], True, "scroll needs_llm=True")

    # ============================
    # 7. Duplicate action detection
    # ============================
    print(f"\n{yellow('=== 7. Duplicate Action Detection ===')}")
    dup_state = {
        "generated_action": {"skill": "click", "params": {"ref": "e1"}},
        "finished_steps": [
            '{"params": {"ref": "e1"}, "skill": "click"}',
            '{"params": {"ref": "e1"}, "skill": "click"}',
        ],
    }
    r6 = _detect_duplicate_action(dup_state)
    failures += not assert_not_none(r6, "duplicate action detected")
    if r6:
        failures += not assert_equals(r6["failure_scope"], "global", "duplicate → global")
        failures += not assert_equals(r6["decision_source"], "duplicate_action", "duplicate → decision_source")

    non_dup_state = {
        "generated_action": {"skill": "snapshot"},
        "finished_steps": ["extract data from list", "click submit button"],
    }
    r7 = _detect_duplicate_action(non_dup_state)
    failures += not assert_is_none(r7, "non-duplicate → no detection")

    # ============================
    # 8. Action kind classification
    # ============================
    print(f"\n{yellow('=== 8. Action Kind Classification ===')}")
    failures += not assert_equals(_dpcli_action_kind({"skill": "snapshot"}), "observation", "snapshot → observation")
    failures += not assert_equals(_dpcli_action_kind({"skill": "extract"}), "data", "extract → data")
    failures += not assert_equals(_dpcli_action_kind({"skill": "click"}), "page", "click → page")
    failures += not assert_equals(_dpcli_action_kind({"skill": "open"}), "page", "open → page")

    # ============================
    # Summary
    # ============================
    print(f"\n{'='*50}")
    if failures == 0:
        print(green(f"\n  ALL TESTS PASSED ({failures} failures)"))
    else:
        print(red(f"\n  {failures} TEST(S) FAILED"))
    print(f"{'='*50}\n")

    return failures


if __name__ == "__main__":
    sys.exit(min(run_tests(), 127))
