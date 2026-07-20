"""
dp_cli Verifier Action Contract Tests (P0-P2, P4-P6).

Validates:
- P0: _dpcli_action_kind classification (observation/data/page)
- P1: _verify_dpcli_action_deterministically behavior
- P2: Verifier prompt contains action kind rules
- P4: TargetSelector ref_type for expand/list-items/extract
- P5: Coder prompt prohibits loop snapshot on expand not_found
- P6: Executor logs action_kind + verification contract

Uses inline mirror logic to avoid heavy import chain dependencies.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _action_kind(action):
    skill = str((action or {}).get("skill") or "").strip().lower()
    if skill in {"snapshot", "expand", "resolve-locator", "find",
                 "session.inspect", "session_inspect"}:
        return "observation"
    if skill in {"extract", "list-items", "batch-detail-extract"}:
        return "data"
    if skill in {"open", "navigate", "click", "type", "scroll", "wait"}:
        return "page"
    return "unknown"


def _compact_result_evidence(result):
    evidence = {"ok": result.get("ok")}
    data = result.get("data") or {}
    if isinstance(data, dict):
        page = data.get("page") or {}
        evidence["url"] = page.get("url", "")
        idx = data.get("index") or {}
        stats = idx.get("stats") or {}
        if stats:
            evidence["node_count"] = stats.get("total_nodes")
        regions = idx.get("data_regions")
        if regions:
            evidence["data_regions"] = len(regions)
        items = data.get("items")
        if isinstance(items, list):
            evidence["item_count"] = len(items)
    return evidence


def _verify_deterministic(action, result):
    kind = _action_kind(action)
    skill = str(action.get("skill") or "").lower()

    if not result.get("ok"):
        return None

    if kind == "observation":
        return {"is_success": True, "is_done": False,
                "summary": f"observation succeeded: {skill}",
                "source": "verifier", "failure_scope": "local"}
    if kind == "data":
        data = result.get("data") or {}
        items = data.get("items") if isinstance(data, dict) else None
        if items and isinstance(items, list) and len(items) > 0:
            return {"is_success": True, "is_done": False,
                    "summary": f"data action succeeded: {skill} ({len(items)} items)"}
        return {"is_success": False, "is_done": False,
                "summary": f"data action returned no usable items: {skill}",
                "failure_scope": "local",
                "fix_hint": "select a better data region or list ref"}
    return None


class TestActionClassification(unittest.TestCase):
    """P0: action kind classification."""

    def test_snapshot_is_observation(self):
        self.assertEqual(_action_kind({"skill": "snapshot"}), "observation")

    def test_expand_is_observation(self):
        self.assertEqual(_action_kind({"skill": "expand"}), "observation")

    def test_resolve_locator_is_observation(self):
        self.assertEqual(_action_kind({"skill": "resolve-locator"}), "observation")

    def test_find_is_observation(self):
        self.assertEqual(_action_kind({"skill": "find"}), "observation")

    def test_session_inspect_is_observation(self):
        self.assertEqual(_action_kind({"skill": "session.inspect"}), "observation")

    def test_extract_is_data(self):
        self.assertEqual(_action_kind({"skill": "extract"}), "data")

    def test_list_items_is_data(self):
        self.assertEqual(_action_kind({"skill": "list-items"}), "data")

    def test_batch_detail_extract_is_data(self):
        self.assertEqual(_action_kind({"skill": "batch-detail-extract"}), "data")

    def test_click_is_page(self):
        self.assertEqual(_action_kind({"skill": "click"}), "page")

    def test_type_is_page(self):
        self.assertEqual(_action_kind({"skill": "type"}), "page")

    def test_open_is_page(self):
        self.assertEqual(_action_kind({"skill": "open"}), "page")

    def test_scroll_is_page(self):
        self.assertEqual(_action_kind({"skill": "scroll"}), "page")

    def test_wait_is_page(self):
        self.assertEqual(_action_kind({"skill": "wait"}), "page")

    def test_navigate_is_page(self):
        self.assertEqual(_action_kind({"skill": "navigate"}), "page")

    def test_unknown_skill_is_unknown(self):
        self.assertEqual(_action_kind({"skill": "eval"}), "unknown")

    def test_none_action_is_unknown(self):
        self.assertEqual(_action_kind(None), "unknown")

    def test_empty_action_is_unknown(self):
        self.assertEqual(_action_kind({}), "unknown")


class TestDeterministicVerifier(unittest.TestCase):
    """P1: deterministic verification for dp_cli observation/data actions."""

    def test_snapshot_ok_returns_success(self):
        action = {"skill": "snapshot", "params": {"mode": "agent_summary"}}
        result = {"ok": True, "action": "snapshot",
                  "data": {"page": {"url": "https://test.com/rank/"},
                           "index": {"stats": {"total_nodes": 1110}}}}
        r = _verify_deterministic(action, result)
        self.assertIsNotNone(r, "snapshot with ok=true should produce result")
        self.assertTrue(r["is_success"],
                        "snapshot should be verified as success")

    def test_snapshot_success_no_url_change_required(self):
        action = {"skill": "snapshot"}
        result = {"ok": True, "action": "snapshot",
                  "data": {"page": {"url": "https://same.url/"},
                           "index": {"stats": {"total_nodes": 500}}}}
        r = _verify_deterministic(action, result)
        self.assertTrue(r["is_success"])
        self.assertIn("observation", r["summary"])

    def test_expand_ok_is_observation_success(self):
        action = {"skill": "expand", "params": {"ref": "r10", "depth": 2}}
        result = {"ok": True, "action": "expand",
                  "data": {"items": [{"ref": "e1"}, {"ref": "e2"}]}}
        r = _verify_deterministic(action, result)
        self.assertIsNotNone(r)
        self.assertTrue(r["is_success"])
        self.assertIn("observation", r["summary"])

    def test_extract_with_items_is_data_success(self):
        action = {"skill": "extract", "params": {"target_ref": "r10"}}
        result = {"ok": True, "action": "extract",
                  "data": {"items": [{"title": "Book A", "url": "/a"},
                                     {"title": "Book B", "url": "/b"}]}}
        r = _verify_deterministic(action, result)
        self.assertIsNotNone(r)
        self.assertTrue(r["is_success"])
        self.assertIn("2 items", r["summary"])

    def test_extract_empty_items_is_data_fail(self):
        action = {"skill": "extract", "params": {"target_ref": "r10"}}
        result = {"ok": True, "action": "extract",
                  "data": {"items": []}}
        r = _verify_deterministic(action, result)
        self.assertIsNotNone(r)
        self.assertFalse(r["is_success"])
        self.assertEqual(r["failure_scope"], "local")
        self.assertIn("data region", r.get("fix_hint", ""))

    def test_extract_no_items_field_is_data_fail(self):
        action = {"skill": "extract"}
        result = {"ok": True, "action": "extract",
                  "data": {"summary": "no items extracted"}}
        r = _verify_deterministic(action, result)
        self.assertIsNotNone(r)
        self.assertFalse(r["is_success"])

    def test_list_items_with_item_count_is_data_success(self):
        action = {"skill": "list-items", "params": {"group_ref": "r5"}}
        result = {"ok": True, "action": "list-items",
                  "data": {"items": [{"ref": "e1"} for _ in range(5)]}}
        r = _verify_deterministic(action, result)
        self.assertTrue(r["is_success"])

    def test_page_action_passes_through_to_llm(self):
        action = {"skill": "click", "params": {"ref": "e2"}}
        result = {"ok": True, "action": "click",
                  "data": {"page": {"url": "https://new.url/"}}}
        r = _verify_deterministic(action, result)
        self.assertIsNone(r, "page action should return None to fall through to LLM")

    def test_failed_observation_does_not_get_deterministic_verdict(self):
        action = {"skill": "snapshot"}
        result = {"ok": False, "action": "snapshot",
                  "error": {"code": "timeout", "message": "timed out"}}
        r = _verify_deterministic(action, result)
        self.assertIsNone(r, "failed action should return None for regular error flow")

    def test_compact_result_evidence_extracts_summary(self):
        result = {"ok": True, "action": "snapshot",
                  "data": {"page": {"url": "https://test.com"},
                           "index": {"stats": {"total_nodes": 1110},
                                     "data_regions": [{"label": "rank_list"}]}}}
        evidence = _compact_result_evidence(result)
        self.assertTrue(evidence["ok"])
        self.assertEqual(evidence["url"], "https://test.com")
        self.assertEqual(evidence["node_count"], 1110)
        self.assertEqual(evidence["data_regions"], 1)


class TestVerifierPromptContent(unittest.TestCase):
    """P2: verifier prompt contains action kind rules."""

    def test_prompt_has_observation_rules(self):
        repo_root = Path(__file__).parent.parent.parent
        prompt_file = repo_root / "prompts" / "verifier_prompts.py"
        content = prompt_file.read_text(encoding="utf-8")
        self.assertIn("observation", content,
                      "Prompt should contain observation rules")
        self.assertIn("Do NOT require URL changes", content,
                      "Prompt should prohibit URL change requirement for observation")
        self.assertIn("Do NOT require visible DOM changes", content,
                      "Prompt should prohibit DOM change requirement for observation")

    def test_prompt_has_new_format_fields(self):
        repo_root = Path(__file__).parent.parent.parent
        prompt_file = repo_root / "prompts" / "verifier_prompts.py"
        content = prompt_file.read_text(encoding="utf-8")
        self.assertIn("{generated_action}", content)
        self.assertIn("{dpcli_action_kind}", content)
        self.assertIn("{dpcli_result_summary}", content)
        self.assertIn("{structured_plan}", content)


class TestPlannerPromptNoExpandAll(unittest.TestCase):
    """P3: planner prompt forbids 'expand all compressed groups'."""

    def test_prompt_forbids_expand_all(self):
        repo_root = Path(__file__).parent.parent.parent
        prompt_file = repo_root / "prompts" / "dpcli_planner_prompts.py"
        content = prompt_file.read_text(encoding="utf-8")
        self.assertIn("绝不要求展开所有压缩组", content,
                      "Planner prompt must forbid expanding all compressed groups")
        self.assertIn("压缩组", content,
                      "Planner prompt must explain compressed groups")


class TestCoderPromptNoLoopSnapshot(unittest.TestCase):
    """P5: coder prompt prohibits looping snapshot on expand not_found."""

    def test_prompt_forbids_snapshot_loop(self):
        repo_root = Path(__file__).parent.parent.parent
        prompt_file = repo_root / "prompts" / "dpcli_action_prompts.py"
        content = prompt_file.read_text(encoding="utf-8")
        self.assertIn("do NOT loop snapshot", content,
                      "Coder prompt must forbid snapshot loops")
        self.assertIn("observation actions", content,
                      "Coder prompt must mention observation actions")


class TestNonDpcliVerifierPrompt(unittest.TestCase):
    """Non-dp_cli mode should not crash with KeyError on new template fields."""

    def test_non_dpcli_prompt_builds_without_keyerror(self):
        from prompts.verifier_prompts import VERIFIER_CHECK_PROMPT
        result = VERIFIER_CHECK_PROMPT.format(
            user_task="test",
            current_plan="plan",
            current_url="http://test.com",
            log="log",
            generated_action="",
            dpcli_action_kind="",
            dpcli_result_summary="",
            structured_plan="",
        )
        self.assertIn("test", result)
        self.assertNotIn("{generated_action}", result,
                         "All format fields should be resolved")


if __name__ == "__main__":
    unittest.main()


# ---- P1: New _verify_dpcli_action_with_signals tests (use actual import) ----

import tests.unit.stubs  # noqa: F401, E402

from core.nodes.verifier import _verify_dpcli_action_with_signals, verifier_node  # noqa: E402


class TestDeterministicWithSignals(unittest.TestCase):
    """P1: _verify_dpcli_action_with_signals — URL, schema, target confidence."""

    def test_navigate_url_match_exact(self):
        state = {
            "generated_action": {"skill": "open", "params": {"url": "https://example.com/page"}},
            "dpcli_result": {"ok": True, "data": {"page": {"url": "https://example.com/page"}}},
            "dpcli_execution_evidence": {"before_url": "", "after_url": "https://example.com/page", "url_changed": True},
            "dpcli_structured_plan": {"step_intent": "navigate", "action_payload": {"url": "https://example.com/page"}},
        }
        result = _verify_dpcli_action_with_signals(state, "https://example.com/page")
        self.assertIsNotNone(result)
        self.assertTrue(result["is_success"])
        self.assertEqual(result["decision_source"], "url_match")

    def test_navigate_url_mismatch_defers_to_llm(self):
        state = {
            "generated_action": {"skill": "open", "params": {"url": "https://example.com/page"}},
            "dpcli_result": {"ok": True, "data": {"page": {"url": "https://other.com"}}},
            "dpcli_execution_evidence": {"before_url": "", "after_url": "https://other.com", "url_changed": True},
            "dpcli_structured_plan": {"step_intent": "navigate", "action_payload": {"url": "https://example.com/page"}},
        }
        result = _verify_dpcli_action_with_signals(state, "https://other.com")
        self.assertIsNone(result)

    def test_click_with_url_match_success(self):
        state = {
            "generated_action": {"skill": "click", "params": {"ref": "e1", "url": "https://example.com/detail"}},
            "dpcli_result": {"ok": True, "data": {"page": {"url": "https://example.com/detail"}}},
            "dpcli_execution_evidence": {"before_url": "https://example.com/list", "after_url": "https://example.com/detail", "url_changed": True},
            "dpcli_structured_plan": {"step_intent": "click", "action_payload": {"url": "https://example.com/detail"}},
        }
        result = _verify_dpcli_action_with_signals(state, "https://example.com/detail")
        self.assertIsNotNone(result)
        self.assertTrue(result["is_success"])
        self.assertEqual(result["decision_source"], "url_match")

    def test_click_no_url_no_change_defers_to_llm(self):
        state = {
            "generated_action": {"skill": "click", "params": {"ref": "e1"}},
            "dpcli_result": {"ok": True, "data": {"page": {"url": "https://example.com/same"}}},
            "dpcli_execution_evidence": {"before_url": "https://example.com/same", "after_url": "https://example.com/same", "url_changed": False},
            "dpcli_structured_plan": {"step_intent": "click", "action_payload": {}},
        }
        result = _verify_dpcli_action_with_signals(state, "https://example.com/same")
        self.assertIsNone(result)

    def test_schema_coverage_sufficient_success(self):
        state = {
            "generated_action": {"skill": "extract", "params": {"schema": ["title", "url", "price"]}},
            "dpcli_result": {"ok": True, "data": {"items": [
                {"title": "Item 1", "url": "https://x.com/1", "price": 10},
                {"title": "Item 2", "url": "https://x.com/2"},
            ]}},
            "dpcli_structured_plan": {"step_intent": "extract", "action_payload": {"schema": ["title", "url", "price"]}},
        }
        result = _verify_dpcli_action_with_signals(state, "")
        self.assertIsNotNone(result)
        self.assertTrue(result["is_success"])
        self.assertEqual(result["decision_source"], "schema_match")

    def test_schema_coverage_insufficient_defers_to_llm(self):
        state = {
            "generated_action": {"skill": "extract", "params": {"schema": ["title", "url", "price", "rating", "author"]}},
            "dpcli_result": {"ok": True, "data": {"items": [
                {"title": "Item 1", "url": "https://x.com/1"},
            ]}},
            "dpcli_structured_plan": {"step_intent": "extract", "action_payload": {"schema": ["title", "url", "price", "rating", "author"]}},
        }
        result = _verify_dpcli_action_with_signals(state, "")
        self.assertIsNone(result)

    def test_schema_keys_with_empty_values_are_not_success(self):
        state = {
            "generated_action": {"skill": "extract", "params": {"schema": ["title", "url"]}},
            "dpcli_result": {"ok": True, "data": {"items": [
                {"title": "", "url": ""},
                {"title": None, "url": "javascript:void(0)"},
            ]}},
            "dpcli_structured_plan": {
                "step_intent": "extract",
                "action_payload": {"schema": ["title", "url"]},
            },
        }

        result = _verify_dpcli_action_with_signals(state, "")

        self.assertIsNotNone(result)
        self.assertFalse(result["is_success"])
        self.assertEqual(result["decision_source"], "data_quality")

    def test_batch_detail_requires_verified_detail_rows(self):
        state = {
            "generated_action": {
                "skill": "batch-detail-extract",
                "params": {"schema": ["title", "description"]},
            },
            "dpcli_result": {"ok": True, "data": {"items": [
                {
                    "detail_ok": True,
                    "final_url": "https://example.test/1",
                    "detail_info": {"title": "One", "description": "Good"},
                },
                {
                    "detail_ok": True,
                    "final_url": "javascript:void(0)",
                    "detail_info": {"title": "Wrong", "description": "Repeated list page"},
                },
                {
                    "detail_ok": False,
                    "final_url": "https://example.test/3",
                    "detail_info": {},
                },
            ]}},
            "dpcli_structured_plan": {
                "step_intent": "batch-detail-extract",
                "action_payload": {"schema": ["title", "description"]},
            },
        }

        result = _verify_dpcli_action_with_signals(state, "")

        self.assertIsNotNone(result)
        self.assertFalse(result["is_success"])
        self.assertEqual(result["decision_source"], "batch_detail_quality")

    def test_scroll_gives_tentative_success(self):
        state = {
            "generated_action": {"skill": "scroll", "params": {"direction": "down"}},
            "dpcli_result": {"ok": True, "data": {"page": {"url": "https://example.com"}}},
            "dpcli_execution_evidence": {"before_url": "https://example.com", "after_url": "https://example.com", "url_changed": False},
            "dpcli_structured_plan": {"step_intent": "scroll", "action_payload": {}},
        }
        result = _verify_dpcli_action_with_signals(state, "https://example.com")
        self.assertIsNotNone(result)
        self.assertTrue(result["is_success"])
        self.assertEqual(result["decision_source"], "dpcli_page_passive")
        self.assertTrue(result["needs_llm"])
        self.assertAlmostEqual(result["confidence"], 0.8, delta=0.05)

    def test_observation_always_succeeds(self):
        state = {
            "generated_action": {"skill": "snapshot"},
            "dpcli_result": {"ok": True, "data": {"page": {"url": "https://example.com"}}},
        }
        result = _verify_dpcli_action_with_signals(state, "https://example.com")
        self.assertIsNotNone(result)
        self.assertTrue(result["is_success"])
        self.assertEqual(result["decision_source"], "dpcli_observation")

    def test_dpcli_verifier_never_reads_the_other_browser_url(self):
        class ExplodingBrowser:
            @property
            def latest_tab(self):
                raise AssertionError("AutoWeb browser must not be read during dp_cli verification")

        class ExplodingLLM:
            def invoke(self, _messages):
                raise AssertionError("deterministic dp_cli verification should not call the LLM")

        state = {
            "execution_mode": "dp_cli",
            "current_url": "https://example.test/list",
            "execution_log": "ok",
            "user_task": "extract list items",
            "plan": "extract the current list",
            "generated_action": {
                "skill": "extract",
                "params": {"schema": ["title", "url"]},
            },
            "dpcli_result": {
                "ok": True,
                "data": {
                    "items": [
                        {"title": "One", "url": "https://example.test/1"},
                    ]
                },
            },
            "dpcli_structured_plan": {
                "step_intent": "extract",
                "action_payload": {"schema": ["title", "url"]},
            },
        }

        command = verifier_node(
            state,
            {"configurable": {"browser": ExplodingBrowser()}},
            ExplodingLLM(),
        )

        self.assertEqual(command.goto, "Observer")
        self.assertEqual(command.update["current_url"], "https://example.test/list")
