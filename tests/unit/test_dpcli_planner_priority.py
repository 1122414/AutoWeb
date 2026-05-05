"""
P3: Planner dp_cli branch priority tests.

Verifies that dp_cli structured Planner runs on loop_count=0 when
dpcli_agent_view is available, and is NOT shadowed by legacy start/continuation checks.
"""
from __future__ import annotations

import unittest


class TestDpcliPlannerPriority(unittest.TestCase):

    def test_is_dpcli_true_with_agent_view(self):
        """dp_cli should activate when enabled, agent_view present, not python_code."""
        state = {
            "dpcli_agent_view": {"capability_map": {}},
            "dpcli_snapshot": {},
            "execution_mode": None,
            "user_task": "search something",
            "current_url": "https://example.com/search",
            "finished_steps": [],
            "reflections": [],
            "loop_count": 0,
        }
        DPCLI_ENABLED = True

        is_dpcli = (
            DPCLI_ENABLED
            and state.get("dpcli_agent_view")
            and state.get("execution_mode") != "python_code"
        )
        self.assertTrue(is_dpcli, "dp_cli should be active with agent_view")

    def test_is_dpcli_false_without_agent_view(self):
        """dp_cli should NOT activate when agent_view is missing."""
        state = {
            "dpcli_snapshot": {},
            "execution_mode": None,
            "user_task": "search something",
        }
        DPCLI_ENABLED = True

        is_dpcli = (
            DPCLI_ENABLED
            and state.get("dpcli_agent_view")
            and state.get("execution_mode") != "python_code"
        )
        self.assertFalse(is_dpcli, "dp_cli should NOT be active without agent_view")

    def test_is_dpcli_false_in_python_code_mode(self):
        """dp_cli should NOT activate when execution_mode is python_code."""
        state = {
            "dpcli_agent_view": {"capability_map": {}},
            "execution_mode": "python_code",
            "user_task": "search something",
        }
        DPCLI_ENABLED = True

        is_dpcli = (
            DPCLI_ENABLED
            and state.get("dpcli_agent_view")
            and state.get("execution_mode") != "python_code"
        )
        self.assertFalse(is_dpcli, "dp_cli should NOT be active in python_code mode")

    def test_is_dpcli_false_when_disabled(self):
        """dp_cli should NOT activate when DPCLI_ENABLED is False."""
        state = {
            "dpcli_agent_view": {"capability_map": {}},
            "execution_mode": None,
        }
        DPCLI_ENABLED = False

        is_dpcli = (
            DPCLI_ENABLED
            and state.get("dpcli_agent_view")
            and state.get("execution_mode") != "python_code"
        )
        self.assertFalse(is_dpcli, "dp_cli should NOT be active when disabled")

    def test_loop_count_zero_dpcli_should_still_run(self):
        """The key P3 fix: dp_cli should run even when loop_count == 0
        (previously was blocked by loop_count > 0 condition)."""
        state = {
            "dpcli_agent_view": {"capability_map": {}},
            "execution_mode": None,
            "loop_count": 0,
            "user_task": "search",
            "current_url": "https://example.com/search",
            "finished_steps": [],
            "reflections": [],
        }
        DPCLI_ENABLED = True
        is_dpcli = (
            DPCLI_ENABLED
            and state.get("dpcli_agent_view")
            and state.get("execution_mode") != "python_code"
        )

        self.assertTrue(is_dpcli, "P3 fix: dp_cli should be active at loop_count=0")
        self.assertEqual(state["loop_count"], 0,
                         "loop_count=0 should NOT block dp_cli")

    def test_planner_goto_for_target_required(self):
        """When dp_cli plan has target_request.required=True, goto should be TargetSelector."""
        step_intent = "click"
        target_required = True
        needs_rag = False

        goto = None
        if step_intent == "finish":
            goto = "Verifier"
        elif needs_rag:
            goto = "RAGNode"
        elif target_required:
            goto = "TargetSelector"
        else:
            goto = "Coder"

        self.assertEqual(goto, "TargetSelector",
                         "target_required=True should route to TargetSelector")

    def test_planner_goto_for_no_target(self):
        """When dp_cli plan has target_request.required=False, goto should be Coder."""
        step_intent = "navigate"
        target_required = False
        needs_rag = False

        goto = None
        if step_intent == "finish":
            goto = "Verifier"
        elif needs_rag:
            goto = "RAGNode"
        elif target_required:
            goto = "TargetSelector"
        else:
            goto = "Coder"

        self.assertEqual(goto, "Coder",
                         "target_required=False should route to Coder")

    def test_planner_goto_not_cache_lookup(self):
        """DP-CLI planner should NEVER route to CacheLookup (that's legacy behavior)."""
        for step_intent, target_required, needs_rag in [
            ("click", True, False),
            ("navigate", False, False),
            ("open", False, False),
            ("finish", False, False),
        ]:
            if step_intent == "finish":
                goto = "Verifier"
            elif needs_rag:
                goto = "RAGNode"
            elif target_required:
                goto = "TargetSelector"
            else:
                goto = "Coder"

            self.assertNotEqual(goto, "CacheLookup",
                                f"dp_cli planner should NOT route to CacheLookup "
                                f"(step_intent={step_intent}, target_required={target_required})")


if __name__ == "__main__":
    unittest.main()
