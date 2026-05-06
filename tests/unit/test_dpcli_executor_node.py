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

    # ---- P0-3: dpcli_execution_evidence ----

    def test_success_writes_execution_evidence(self):
        state = {
            "generated_action": {"skill": "click", "params": {"ref": "e1"}},
            "dpcli_session": "unit",
            "current_url": "https://example.test/before",
        }
        result_payload = {
            "ok": True, "session": "unit", "action": "click",
            "data": {"page": {"url": "https://example.test/after"}},
        }
        with patch("skills.dpcli_executor.DPCLIExecutor") as executor_cls:
            executor_cls.return_value.execute_action.return_value = result_payload
            command = _executor_dpcli_branch(state, {"configurable": {}})

        evidence = command.update["dpcli_execution_evidence"]
        self.assertEqual(evidence["before_url"], "https://example.test/before")
        self.assertEqual(evidence["after_url"], "https://example.test/after")
        self.assertTrue(evidence["url_changed"])
        self.assertEqual(evidence["action_skill"], "click")
        self.assertTrue(evidence["result_ok"])

    def test_url_unchanged_when_same(self):
        state = {
            "generated_action": {"skill": "click", "params": {"ref": "e1"}},
            "dpcli_session": "unit",
            "current_url": "https://example.test/same",
        }
        result_payload = {
            "ok": True, "session": "unit", "action": "click",
            "data": {"page": {"url": "https://example.test/same"}},
        }
        with patch("skills.dpcli_executor.DPCLIExecutor") as executor_cls:
            executor_cls.return_value.execute_action.return_value = result_payload
            command = _executor_dpcli_branch(state, {"configurable": {}})

        evidence = command.update["dpcli_execution_evidence"]
        self.assertFalse(evidence["url_changed"])
        self.assertEqual(evidence["before_url"], evidence["after_url"])

    def test_empty_before_url_url_unchanged(self):
        state = {
            "generated_action": {"skill": "open", "params": {"url": "https://x.com"}},
            "dpcli_session": "unit",
            "current_url": "",
        }
        result_payload = {
            "ok": True, "session": "unit", "action": "open",
            "data": {"page": {"url": "https://x.com"}},
        }
        with patch("skills.dpcli_executor.DPCLIExecutor") as executor_cls:
            executor_cls.return_value.execute_action.return_value = result_payload
            command = _executor_dpcli_branch(state, {"configurable": {}})

        evidence = command.update["dpcli_execution_evidence"]
        self.assertEqual(evidence["before_url"], "")
        self.assertFalse(evidence["url_changed"])

    def test_failure_path_writes_evidence(self):
        state = {
            "generated_action": {"skill": "click", "params": {"ref": "e1"}},
            "dpcli_session": "unit",
            "current_url": "https://example.test",
        }
        result_payload = {
            "ok": False, "session": "unit", "action": "click",
            "data": None,
            "error": {"code": "ref_stale", "message": "stale"},
        }
        with patch("skills.dpcli_executor.DPCLIExecutor") as executor_cls:
            executor_cls.return_value.execute_action.return_value = result_payload
            command = _executor_dpcli_branch(state, {"configurable": {}})

        self.assertIn("dpcli_execution_evidence", command.update)
        evidence = command.update["dpcli_execution_evidence"]
        self.assertFalse(evidence["result_ok"])


if __name__ == "__main__":
    unittest.main()
