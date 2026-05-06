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

    # ---- P0-1: Extended verification_result fields ----

    def test_verification_result_default_confidence(self):
        vr = self.nodes._build_verification_result(
            is_success=True, summary="ok", source="verifier",
        )
        self.assertEqual(vr["confidence"], 1.0)
        self.assertEqual(vr["decision_source"], "")
        self.assertFalse(vr["needs_llm"])
        self.assertEqual(vr["warnings"], [])

        vr_fail = self.nodes._build_verification_result(
            is_success=False, summary="fail", source="verifier",
        )
        self.assertEqual(vr_fail["confidence"], 0.0)

    def test_verification_result_explicit_new_fields(self):
        vr = self.nodes._build_verification_result(
            is_success=True, summary="ok", source="verifier",
            confidence=0.85, decision_source="url_match",
            needs_llm=True, warnings=["detail batch skipped"],
        )
        self.assertEqual(vr["confidence"], 0.85)
        self.assertEqual(vr["decision_source"], "url_match")
        self.assertTrue(vr["needs_llm"])
        self.assertEqual(vr["warnings"], ["detail batch skipped"])

    def test_coerce_passes_through_new_fields(self):
        vr = self.nodes._coerce_verification_result(
            {"confidence": 0.75, "decision_source": "error_type",
             "needs_llm": True, "warnings": ["w1"]},
            fallback_is_success=False,
        )
        self.assertEqual(vr["confidence"], 0.75)
        self.assertEqual(vr["decision_source"], "error_type")
        self.assertTrue(vr["needs_llm"])
        self.assertEqual(vr["warnings"], ["w1"])

    def test_coerce_defaults_new_fields(self):
        vr = self.nodes._coerce_verification_result(
            {},
            fallback_is_success=True,
        )
        self.assertEqual(vr["confidence"], 1.0)
        self.assertEqual(vr["decision_source"], "")
        self.assertFalse(vr["needs_llm"])
        self.assertEqual(vr["warnings"], [])

    # ---- P0-2: Robust parser ----

    def test_parser_space_before_colon(self):
        content = "Status : STEP_SUCCESS\nSummary: ok"
        parsed = self.nodes._parse_verifier_result_content(content)
        self.assertTrue(parsed["is_success"])

    def test_parser_lowercase(self):
        content = "status: step_fail\nSummary: error"
        parsed = self.nodes._parse_verifier_result_content(content)
        self.assertFalse(parsed["is_success"])

    def test_parser_no_space_after_colon(self):
        content = "Status:STEP_FAIL\nSummary:bad"
        parsed = self.nodes._parse_verifier_result_content(content)
        self.assertFalse(parsed["is_success"])
        self.assertEqual(parsed["summary"], "bad")

    def test_parser_extra_whitespace(self):
        content = "  Status:   STEP_SUCCESS   \n  Summary :   done  "
        parsed = self.nodes._parse_verifier_result_content(content)
        self.assertTrue(parsed["is_success"])
        self.assertEqual(parsed["summary"], "done")

    def test_normalize_source_new_values(self):
        self.assertEqual(
            self.nodes._normalize_verification_source("url_match"), "url_match")
        self.assertEqual(
            self.nodes._normalize_verification_source("target_confidence"), "target_confidence")
        self.assertEqual(
            self.nodes._normalize_verification_source("error_type"), "error_type")


if __name__ == "__main__":
    unittest.main()
