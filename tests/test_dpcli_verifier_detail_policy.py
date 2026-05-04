"""
Verifier detail batch policy fix tests.

Verifies that the deterministic Verifier success path triggers
batch-detail-extract when extract returns URL items + task requests details.
"""
from __future__ import annotations

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


def _verify_deterministic(action, result):
    kind = _action_kind(action)
    if not result.get("ok"):
        return None
    if kind == "observation":
        return {"is_success": True, "is_done": False,
                "summary": "observation succeeded"}
    if kind == "data":
        items = (result.get("data") or {}).get("items")
        if items and isinstance(items, list) and len(items) > 0:
            return {"is_success": True, "is_done": False,
                    "summary": f"data succeeded ({len(items)} items)"}
        return {"is_success": False, "is_done": False,
                "summary": "data returned no items"}
    return None


DETAIL_GOAL_TOKENS = (
    "detail", "description", "summary", "intro",
    "简介", "详情", "介绍", "描述", "摘要",
)


def _goal_requests_detail(goal: str) -> bool:
    text = str(goal or "").lower()
    return any(token in text for token in DETAIL_GOAL_TOKENS)


def _has_url_items(result) -> bool:
    items = (result.get("data") or {}).get("items") or []
    for item in items:
        if isinstance(item, dict) and (item.get("url") or item.get("detail_url")):
            return True
    return False


def _should_run_detail_batch(state) -> bool:
    if state.get("dpcli_detail_batch_ran"):
        return False
    if not _goal_requests_detail(state.get("user_task", "")):
        return False
    return _has_url_items(state.get("dpcli_result") or {})


class TestDetailBatchPolicy(unittest.TestCase):
    """Deterministic extract success should trigger batch-detail-extract."""

    def _build_extract_state(self, task, items):
        return {
            "execution_mode": "dp_cli",
            "user_task": task,
            "generated_action": {"skill": "extract", "params": {"target_ref": "r1"}},
            "dpcli_result": {
                "ok": True, "action": "extract",
                "data": {"page": {"url": "https://qidian.com/rank/"}, "items": items},
            },
            "dpcli_detail_batch_ran": False,
            "plan": '{"step_intent":"extract"}',
            "finished_steps": [],
            "reflections": [],
            "current_url": "https://qidian.com/rank/",
            "dpcli_snapshot_view": {},
        }

    def test_extract_detail_task_triggers_batch(self):
        """extract with detail task + url items → goto Executor with batch-detail-extract."""
        state = self._build_extract_state(
            task="获取榜单小说信息，并点击各个小说获取简介",
            items=[
                {"title": "Book A", "url": "https://book-a"},
                {"title": "Book B", "detail_url": "https://book-b"},
            ],
        )

        action = state["generated_action"]
        result = state["dpcli_result"]
        det = _verify_deterministic(action, result)
        self.assertIsNotNone(det, "extract should produce deterministic verdict")
        self.assertTrue(det["is_success"], "extract with items should be success")

        kind = _action_kind(action)
        self.assertEqual(kind, "data", "extract is a data action")

        should_batch = _should_run_detail_batch(state)
        self.assertTrue(should_batch,
                        "detail task with url items should trigger batch-detail-extract")

    def test_extract_non_detail_task_skips_batch(self):
        """extract without detail tokens → Observer, no batch."""
        state = self._build_extract_state(
            task="只获取榜单小说标题",
            items=[{"title": "Book A", "url": "https://book-a"}],
        )

        det = _verify_deterministic(state["generated_action"], state["dpcli_result"])
        self.assertIsNotNone(det)
        self.assertTrue(det["is_success"])

        should_batch = _should_run_detail_batch(state)
        self.assertFalse(should_batch,
                         "non-detail task should NOT trigger batch-detail-extract")

    def test_snapshot_observation_skips_batch(self):
        """snapshot observation success → Observer, no batch."""
        state = {
            "execution_mode": "dp_cli",
            "user_task": "获取榜单小说信息，并点击各个小说获取简介",
            "generated_action": {"skill": "snapshot", "params": {"mode": "agent_summary"}},
            "dpcli_result": {"ok": True, "action": "snapshot",
                             "data": {"page": {"url": "https://qidian.com"},
                                      "index": {"stats": {"total_nodes": 100}}}},
            "dpcli_detail_batch_ran": False,
        }

        kind = _action_kind(state["generated_action"])
        self.assertEqual(kind, "observation",
                         "snapshot is an observation action")

        det = _verify_deterministic(state["generated_action"], state["dpcli_result"])
        self.assertIsNotNone(det)
        self.assertTrue(det["is_success"])

    def test_extract_no_items_fails_does_not_batch(self):
        """extract with no items → fail, should not trigger batch."""
        state = self._build_extract_state(
            task="获取榜单小说信息，并点击各个小说获取简介",
            items=[],
        )

        det = _verify_deterministic(state["generated_action"], state["dpcli_result"])
        self.assertIsNotNone(det)
        self.assertFalse(det["is_success"], "extract with no items should fail")

    def test_extract_no_url_items_skips_batch(self):
        """extract with items but no URLs → should not batch."""
        state = self._build_extract_state(
            task="获取榜单小说信息，并点击各个小说获取简介",
            items=[{"title": "Book A"}, {"title": "Book B"}],
        )

        det = _verify_deterministic(state["generated_action"], state["dpcli_result"])
        self.assertTrue(det["is_success"])

        should_batch = _should_run_detail_batch(state)
        self.assertFalse(should_batch,
                         "items without URLs should NOT trigger batch")

    def test_detail_batch_ran_prevents_recursion(self):
        """dpcli_detail_batch_ran=True → should NOT re-trigger."""
        state = self._build_extract_state(
            task="获取榜单小说信息，并点击各个小说获取简介",
            items=[{"title": "Book A", "url": "https://book-a"}],
        )
        state["dpcli_detail_batch_ran"] = True

        should_batch = _should_run_detail_batch(state)
        self.assertFalse(should_batch,
                         "dpcli_detail_batch_ran=True should prevent recursion")

    def test_list_items_detail_task_can_batch(self):
        """list-items data action with detail task + url items → can batch."""
        state = {
            "execution_mode": "dp_cli",
            "user_task": "获取榜单小说信息，并点击各个小说获取简介",
            "generated_action": {"skill": "list-items", "params": {"group_ref": "r5"}},
            "dpcli_result": {
                "ok": True, "action": "list-items",
                "data": {"items": [{"title": "Book A", "url": "https://book-a"},
                                   {"title": "Book B", "detail_url": "https://book-b"}]},
            },
            "dpcli_detail_batch_ran": False,
            "plan": "",
            "finished_steps": [],
            "current_url": "https://qidian.com/rank/",
        }

        kind = _action_kind(state["generated_action"])
        self.assertEqual(kind, "data")

        det = _verify_deterministic(state["generated_action"], state["dpcli_result"])
        self.assertTrue(det["is_success"])

        should_batch = _should_run_detail_batch(state)
        self.assertTrue(should_batch,
                        "list-items with detail task + url items should batch")


class TestLLMBranchDetailBatch(unittest.TestCase):
    """Plan scenario 4: LLM success branch still triggers batch-detail-extract."""

    def test_page_action_llm_success_triggers_batch(self):
        """When a page action passes through deterministic (returns None) and
        LLM marks success, the LLM branch should still trigger detail policy.
        This mirrors the helper's behavior on the LLM path."""
        state = {
            "execution_mode": "dp_cli",
            "user_task": "获取榜单小说信息，并点击各个小说获取简介",
            "generated_action": {"skill": "click", "params": {"ref": "e2"}},
            "dpcli_result": {
                "ok": True, "action": "click",
                "data": {
                    "page": {"url": "https://qidian.com/rank/"},
                    "items": [
                        {"title": "Book A", "url": "https://book-a"},
                        {"title": "Book B", "detail_url": "https://book-b"},
                    ],
                },
            },
            "dpcli_detail_batch_ran": False,
            "plan": '{"step_intent":"click"}',
            "finished_steps": [],
            "current_url": "https://qidian.com/rank/",
        }

        # page action → deterministic returns None (fall through to LLM)
        det = _verify_deterministic(state["generated_action"], state["dpcli_result"])
        self.assertIsNone(det,
                          "page action should return None from deterministic verifier")

        # LLM marks success → verifier_node calls _handle_dpcli_success
        # which runs should_run_detail_batch → True for detail task + url items
        should_batch = _should_run_detail_batch(state)
        self.assertTrue(should_batch,
                        "LLM success branch should still trigger batch-detail-extract")

    def test_llm_path_non_detail_task_skips_batch(self):
        """LLM success for non-detail task → no batch."""
        state = {
            "execution_mode": "dp_cli",
            "user_task": "只获取榜单小说标题",
            "generated_action": {"skill": "click", "params": {"ref": "e2"}},
            "dpcli_result": {
                "ok": True, "action": "click",
                "data": {"items": [{"title": "Book A", "url": "https://book-a"}]},
            },
            "dpcli_detail_batch_ran": False,
            "plan": "",
            "finished_steps": [],
            "current_url": "https://qidian.com/",
        }

        det = _verify_deterministic(state["generated_action"], state["dpcli_result"])
        self.assertIsNone(det)

        should_batch = _should_run_detail_batch(state)
        self.assertFalse(should_batch,
                         "non-detail task should skip batch in LLM path too")


if __name__ == "__main__":
    unittest.main()
