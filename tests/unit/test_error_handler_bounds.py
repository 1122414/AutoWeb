from __future__ import annotations

import unittest
from unittest.mock import Mock

import tests.unit.stubs  # noqa: F401
from core.nodes.error_handler import error_handler_node


class ErrorHandlerBoundsTests(unittest.TestCase):
    def test_first_recovery_attempt_returns_to_observer(self) -> None:
        llm = Mock()
        llm.invoke.return_value.content = "Status: RETRY"

        command = error_handler_node(
            {
                "error": "snapshot failed: body missing",
                "reflections": [],
                "plan": "snapshot",
                "_error_recovery_count": 0,
                "_last_recovery_error": None,
            },
            config={},
            llm=llm,
        )

        self.assertEqual(command.goto, "Observer")
        self.assertEqual(command.update["_error_recovery_count"], 1)
        self.assertEqual(
            command.update["_last_recovery_error"],
            "snapshot failed: body missing",
        )
        llm.invoke.assert_called_once()

    def test_third_identical_error_terminates_without_another_llm_call(self) -> None:
        llm = Mock()

        command = error_handler_node(
            {
                "error": "snapshot failed: body missing",
                "reflections": [],
                "plan": "snapshot",
                "_error_recovery_count": 2,
                "_last_recovery_error": "snapshot failed: body missing",
            },
            config={},
            llm=llm,
        )

        self.assertEqual(command.goto, "__end__")
        self.assertTrue(command.update["is_complete"])
        self.assertEqual(command.update["_error_recovery_count"], 3)
        self.assertIn(
            "连续 3 次",
            command.update["verification_result"]["summary"],
        )
        llm.invoke.assert_not_called()

    def test_different_error_resets_recovery_counter(self) -> None:
        llm = Mock()
        llm.invoke.return_value.content = "Status: RETRY"

        command = error_handler_node(
            {
                "error": "different failure",
                "reflections": [],
                "plan": "snapshot",
                "_error_recovery_count": 2,
                "_last_recovery_error": "old failure",
            },
            config={},
            llm=llm,
        )

        self.assertEqual(command.goto, "Observer")
        self.assertEqual(command.update["_error_recovery_count"], 1)


if __name__ == "__main__":
    unittest.main()
