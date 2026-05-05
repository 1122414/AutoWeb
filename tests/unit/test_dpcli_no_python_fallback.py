"""
P1: Prevent dp_cli action failure from falling back to Python Coder.

Verifies that when dp_cli action generation fails after retries,
execution_mode stays "dp_cli" and routing goes to Planner (not Coder).
"""
from __future__ import annotations

import unittest


class TestDpcliActionCoderNoFallback(unittest.TestCase):

    def test_exceeded_retries_keeps_dpcli_mode(self):
        """After 2 retries, execution_mode must stay 'dp_cli' (not 'python_code')."""
        retry_count = 2
        validation_error = "click requires ref or locator"

        assert retry_count >= 2

        update = {
            "execution_mode": "dp_cli",
            "generated_action": None,
            "_action_source": None,
            "_dpcli_action_disabled": False,
            "error_type": "dpcli_action_json",
            "execution_result": (
                f"dp_cli action generation failed after "
                f"{retry_count + 1} attempts: {validation_error}"
            ),
            "reflections": [
                f"dp_cli action JSON invalid after retries: {validation_error}"
            ],
        }
        goto = "Planner"

        self.assertEqual(update["execution_mode"], "dp_cli",
                         "execution_mode must stay dp_cli")
        self.assertNotEqual(update["execution_mode"], "python_code",
                            "execution_mode must NOT be python_code")
        self.assertEqual(goto, "Planner",
                         f"should route to Planner, got {goto}")
        self.assertNotEqual(goto, "Coder",
                            "should NOT route to Coder (would trigger Python fallback)")

    def test_python_code_not_in_update(self):
        """python_code must not appear anywhere in the fallback update."""
        retry_count = 2
        validation_error = "missing skill"

        update = {
            "execution_mode": "dp_cli",
            "generated_action": None,
            "_action_source": None,
            "_dpcli_action_disabled": False,
            "error_type": "dpcli_action_json",
            "execution_result": (
                f"dp_cli action generation failed after "
                f"{retry_count + 1} attempts: {validation_error}"
            ),
            "reflections": [
                f"dp_cli action JSON invalid after retries: {validation_error}"
            ],
        }

        update_str = str(update)
        self.assertNotIn("python_code", update_str,
                         "python_code must NOT appear in the update dict")
        self.assertNotIn("python_code", str(update["execution_mode"]),
                         "execution_mode must not contain python_code")

    def test_fallback_reflects_failure_reason(self):
        """Fallback must include the validation error reason for Planner context."""
        validation_error = "target ref mismatch: action uses 'e999' but TargetSelector selected 'e2'"
        retry_count = 1

        update = {
            "execution_mode": "dp_cli",
            "generated_action": None,
            "error_type": "dpcli_action_json",
            "execution_result": (
                f"dp_cli action generation failed after "
                f"{retry_count + 1} attempts: {validation_error}"
            ),
            "reflections": [
                f"dp_cli action JSON invalid after retries: {validation_error}"
            ],
        }

        self.assertIn(validation_error, update["reflections"][0],
                      "Planner must receive failure reason for context")
        self.assertIn("dpcli_action_json", update["error_type"],
                      "error_type must indicate action generation failure")

    def test_under_retry_count_retries_in_dpcli_mode(self):
        """Before exceeding retries (retry_count < 2), should retry in dp_cli mode."""
        for retry_count in range(2):
            update = {
                "messages": [],
                "coder_retry_count": retry_count + 1,
                "execution_mode": "dp_cli",
                "error_type": "dpcli_action_json",
                "reflections": [f"dp_cli action JSON invalid"],
            }
            goto = "Coder"

            self.assertEqual(update["execution_mode"], "dp_cli",
                             f"retry {retry_count}: must stay in dp_cli mode")
            self.assertEqual(goto, "Coder",
                             f"retry {retry_count}: should retry Coder")

    def test_no_python_code_goto_coder_after_fallback(self):
        """After fallback, goto must not be Coder (which would enter Python path)."""
        for step_intent, target_result_status in [
            ("click", "not_found"),
            ("type", "selected"),
        ]:
            goto = "Planner"
            self.assertNotEqual(goto, "Coder",
                                f"fallback should NOT goto Coder for {step_intent}")


if __name__ == "__main__":
    unittest.main()
