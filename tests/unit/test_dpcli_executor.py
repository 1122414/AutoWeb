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

    @patch("skills.dpcli_executor.subprocess.run")
    def test_execute_action_dispatches_submit_and_scroll(self, run):
        run.return_value = Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "session": "unit",
                    "action": "ok",
                    "data": {"page": {}},
                    "error": None,
                }
            ),
            stderr="",
        )
        executor = self.make_executor()

        executor.execute_action(
            {
                "skill": "type",
                "params": {"ref": "e1", "text": "Boston", "submit": True},
            }
        )
        type_args = run.call_args.args[0]
        self.assertIn("--submit", type_args)

        executor.execute_action(
            {
                "skill": "scroll",
                "params": {
                    "direction": "down",
                    "amount": 900,
                    "to": "bottom",
                    "wait_time": 1,
                },
            }
        )
        scroll_args = run.call_args.args[0]
        self.assertIn("scroll", scroll_args)
        self.assertEqual(scroll_args[scroll_args.index("--direction") + 1], "down")
        self.assertEqual(scroll_args[scroll_args.index("--amount") + 1], "900")
        self.assertEqual(scroll_args[scroll_args.index("--to") + 1], "bottom")

    @patch("skills.dpcli_executor.subprocess.run")
    def test_execute_action_wait_uses_snapshot_wait_and_preserves_wait_action(self, run):
        payload = {
            "ok": True,
            "session": "unit",
            "action": "snapshot",
            "data": {"page": {"url": "https://example.test"}},
            "error": None,
        }
        run.return_value = Mock(returncode=0, stdout=json.dumps(payload), stderr="")

        result = self.make_executor().execute_action(
            {"skill": "wait", "params": {"seconds": 1.25}}
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "wait")
        args = run.call_args.args[0]
        self.assertIn("snapshot", args)
        self.assertEqual(args[args.index("--wait-time") + 1], "1.25")

    def test_execute_action_rejects_unknown_skill(self):
        result = self.make_executor().execute_action({"skill": "unknown", "params": {}})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_action")

    @patch("skills.dpcli_executor.subprocess.run")
    def test_batch_detail_filters_invalid_and_duplicate_urls(self, run):
        payload = {
            "ok": True,
            "session": "unit",
            "action": "batch-detail-extract",
            "data": {"items": []},
            "error": None,
        }
        run.return_value = Mock(returncode=0, stdout=json.dumps(payload), stderr="")

        result = self.make_executor().batch_detail_extract(
            items=[
                {"title": "A", "url": "https://example.test/book/1/"},
                {"title": "Script", "url": "javascript:"},
                {"title": "Mail", "url": "mailto:hello@example.test"},
                {"title": "Duplicate", "url": "https://example.test/book/1"},
                {"title": "B", "href": "https://example.test/book/2"},
            ]
        )

        self.assertTrue(result["ok"])
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("--extractor") + 1], "legacy-js")
        items_json = args[args.index("--items-json") + 1]
        items = json.loads(items_json)
        self.assertEqual(len(items), 2)
        self.assertEqual([item.get("title") for item in items], ["A", "B"])

    def test_batch_detail_rejects_when_no_valid_urls_remain(self):
        result = self.make_executor().batch_detail_extract(
            items=[
                {"title": "Script", "url": "javascript:"},
                {"title": "Empty", "url": ""},
            ]
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_action")

    @patch("skills.dpcli_executor.subprocess.run")
    def test_session_close_dispatches_without_headless_flag(self, run):
        payload = {
            "ok": True,
            "session": "unit",
            "action": "session.close",
            "data": {"closed": True},
            "error": None,
        }
        run.return_value = Mock(returncode=0, stdout=json.dumps(payload), stderr="")

        result = self.make_executor().session_close()

        self.assertTrue(result["ok"])
        args = run.call_args.args[0]
        self.assertEqual(args[:5], ["python", "-m", "dp_cli", "session", "close"])
        self.assertNotIn("--headless", args)


if __name__ == "__main__":
    unittest.main()
