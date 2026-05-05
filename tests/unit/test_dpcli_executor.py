import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from skills.dpcli_executor import DPCLIExecutor


class DPCLIExecutorTests(unittest.TestCase):
    def make_executor(self):
        return DPCLIExecutor(
            session="unit",
            headless=True,
            python_executable="python",
            cwd="E:\\GitHub\\Repositories\\drissionpage-cli",
            timeout_seconds=5,
            batch_timeout_seconds=20,
        )

    @patch("skills.dpcli_executor.subprocess.run")
    def test_open_builds_command_and_parses_json(self, run):
        payload = {"ok": True, "session": "unit", "action": "open", "data": {"page": {}}, "error": None}
        run.return_value = Mock(returncode=0, stdout=json.dumps(payload), stderr="")

        result = self.make_executor().open("https://example.com", wait_time=1)

        self.assertTrue(result["ok"])
        cmd = run.call_args.kwargs
        self.assertEqual(cmd["cwd"], "E:\\GitHub\\Repositories\\drissionpage-cli")
        args = run.call_args.args[0]
        self.assertEqual(args[:3], ["python", "-m", "dp_cli"])
        self.assertIn("open", args)
        self.assertIn("--headless", args)
        self.assertEqual(args[-2:], ["--session", "unit"])
        self.assertIn("--wait-time", args)

    @patch("skills.dpcli_executor.subprocess.run")
    def test_nonzero_json_error_is_preserved(self, run):
        payload = {
            "ok": False,
            "session": "unit",
            "action": "click",
            "data": None,
            "error": {"code": "ref_stale", "message": "stale", "details": {"ref": "e1"}},
        }
        run.return_value = Mock(returncode=6, stdout=json.dumps(payload), stderr="")

        result = self.make_executor().click(ref="e1")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "ref_stale")

    @patch("skills.dpcli_executor.subprocess.run")
    def test_invalid_json_returns_structured_error(self, run):
        run.return_value = Mock(returncode=0, stdout="not json", stderr="")

        result = self.make_executor().snapshot()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_json")

    @patch("skills.dpcli_executor.subprocess.run")
    def test_timeout_returns_structured_error(self, run):
        run.side_effect = subprocess.TimeoutExpired(["python"], timeout=5, output="", stderr="late")

        result = self.make_executor().find(text="Search")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "timeout")
        self.assertEqual(result["error"]["details"]["timeout"], 5)

    @patch("skills.dpcli_executor.subprocess.run")
    def test_execute_action_dispatches_type(self, run):
        payload = {"ok": True, "session": "unit", "action": "type", "data": {"typed_text": "hello"}, "error": None}
        run.return_value = Mock(returncode=0, stdout=json.dumps(payload), stderr="")

        result = self.make_executor().execute_action(
            {"skill": "type", "params": {"ref": "e1", "text": "hello"}}
        )

        self.assertTrue(result["ok"])
        args = run.call_args.args[0]
        self.assertIn("type", args)
        self.assertIn("--ref", args)
        self.assertIn("--text", args)

    def test_execute_action_rejects_unknown_skill(self):
        result = self.make_executor().execute_action({"skill": "unknown", "params": {}})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_action")


if __name__ == "__main__":
    unittest.main()
