import unittest
import importlib


class VerificationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.nodes = importlib.import_module("core.nodes")
        except Exception as exc:
            raise unittest.SkipTest(f"core.nodes import skipped: {exc}")

    def test_build_verification_result_schema(self):
        vr = self.nodes._build_verification_result(
            is_success=False,
            is_done=True,  # fail must force is_done=False
            summary="step failed",
            source="verifier",
            failure_scope="LOCAL",  # invalid case should normalize
            failed_action="click login",
            failed_locator="#login",
            evidence="Timeout",
            fix_hint="retry locator",
        )
        self.assertEqual(vr["is_success"], False)
        self.assertEqual(vr["is_done"], False)
        self.assertEqual(vr["source"], "verifier")
        self.assertEqual(vr["failure_scope"], "local")
        self.assertEqual(vr["failed_action"], "click login")
        self.assertEqual(vr["failed_locator"], "#login")

    def test_coerce_verification_result_fill_defaults(self):
        vr = self.nodes._coerce_verification_result(
            {},
            fallback_is_success=False,
            fallback_summary="fallback",
            fallback_source="executor",
            fallback_failure_scope="global",
            fallback_failed_action="extract",
        )
        self.assertEqual(vr["summary"], "fallback")
        self.assertEqual(vr["source"], "executor")
        self.assertEqual(vr["failure_scope"], "global")
        self.assertEqual(vr["failed_action"], "extract")

    def test_parse_verifier_fields(self):
        content = "\n".join(
            [
                "Status: STEP_FAIL",
                "Summary: button click failed",
                "FailureScope: GLOBAL",
                "FailedAction: click submit",
                "FailedLocator: x://button[@id='submit']",
                "Evidence: TimeoutException",
                "FixHint: use alternative locator",
            ]
        )
        parsed = self.nodes._parse_verifier_result_content(content)
        self.assertEqual(parsed["is_success"], False)
        self.assertEqual(parsed["summary"], "button click failed")
        self.assertEqual(parsed["failure_scope"], "global")
        self.assertEqual(parsed["failed_action"], "click submit")
        self.assertEqual(parsed["failed_locator"], "x://button[@id='submit']")

    def test_global_rewrite_plan_detector(self):
        self.assertTrue(self.nodes._looks_like_global_rewrite_plan("从头重做整个流程"))
        self.assertFalse(self.nodes._looks_like_global_rewrite_plan("修复提交按钮定位器并重试"))


if __name__ == "__main__":
    unittest.main()
