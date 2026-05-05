import unittest
from unittest.mock import patch

import tests.unit.stubs  # noqa: F401 - installs lightweight dependency stubs

from core.nodes import _executor_dpcli_branch


class DPCLIExecutorNodeTests(unittest.TestCase):
    def test_dpcli_success_goes_to_verifier(self):
        state = {
            "generated_action": {"skill": "click", "params": {"ref": "e1"}},
            "dpcli_session": "unit",
            "current_url": "https://example.test",
        }
        result_payload = {
            "ok": True,
            "session": "unit",
            "action": "click",
            "data": {"page": {"url": "https://example.test/next"}},
            "error": None,
        }
        with patch("skills.dpcli_executor.DPCLIExecutor") as executor_cls:
            executor_cls.return_value.execute_action.return_value = result_payload
            command = _executor_dpcli_branch(state, {"configurable": {}})

        self.assertEqual(command.goto, "Verifier")
        self.assertEqual(command.update["dpcli_result"], result_payload)
        self.assertEqual(command.update["current_url"], "https://example.test/next")
        executor_cls.return_value.execute_action.assert_called_once_with(state["generated_action"])

    def test_ref_stale_goes_to_observer(self):
        state = {
            "generated_action": {"skill": "click", "params": {"ref": "e1"}},
            "dpcli_session": "unit",
        }
        result_payload = {
            "ok": False,
            "session": "unit",
            "action": "click",
            "data": None,
            "error": {"code": "ref_stale", "message": "stale", "details": {"ref": "e1"}},
        }
        with patch("skills.dpcli_executor.DPCLIExecutor") as executor_cls:
            executor_cls.return_value.execute_action.return_value = result_payload
            command = _executor_dpcli_branch(state, {"configurable": {}})

        self.assertEqual(command.goto, "Observer")
        self.assertEqual(command.update["error_type"], "dpcli_ref_stale")
        self.assertFalse(command.update["verification_result"]["is_success"])

    def test_invalid_action_goes_to_coder(self):
        command = _executor_dpcli_branch({"generated_action": None}, {"configurable": {}})

        self.assertEqual(command.goto, "Coder")
        self.assertEqual(command.update["error_type"], "dpcli_invalid_action")


if __name__ == "__main__":
    unittest.main()
