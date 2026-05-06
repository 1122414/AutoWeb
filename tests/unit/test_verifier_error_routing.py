"""P0-4: Verifier error_type routing + regex keyword fallback tests."""
import unittest

import tests.unit.stubs  # noqa: F401

from core.nodes.verifier import _route_by_error_type


class TestErrorTypeRouting(unittest.TestCase):
    """Structured error_type should route before keyword scan."""

    def test_ref_stale_goes_to_observer(self):
        state = {
            "error_type": "dpcli_ref_stale",
            "plan": "click login",
            "locator_suggestions": [{"url": "https://x.com", "strategies": []}],
        }
        cmd = _route_by_error_type(state, "click login", "llm")
        self.assertEqual(cmd.goto, "Observer")
        self.assertEqual(
            cmd.update["verification_result"]["failure_scope"], "local")
        self.assertEqual(
            cmd.update["verification_result"]["decision_source"], "error_type")
        self.assertFalse(cmd.update["verification_result"]["is_success"])

    def test_invalid_action_goes_to_coder(self):
        state = {
            "error_type": "dpcli_invalid_action",
            "plan": "extract data",
            "locator_suggestions": [],
        }
        cmd = _route_by_error_type(state, "extract data", "llm")
        self.assertEqual(cmd.goto, "Coder")
        self.assertEqual(
            cmd.update["verification_result"]["decision_source"], "error_type")

    def test_critical_goes_to_planner_global(self):
        state = {
            "error_type": "critical",
            "plan": "critical step",
            "locator_suggestions": [],
        }
        cmd = _route_by_error_type(state, "critical step", "llm")
        self.assertEqual(cmd.goto, "Planner")
        self.assertEqual(
            cmd.update["verification_result"]["failure_scope"], "global")
        self.assertEqual(
            cmd.update["verification_result"]["decision_source"], "error_type")

    def test_syntax_goes_to_coder(self):
        state = {
            "error_type": "syntax",
            "plan": "broken code",
            "locator_suggestions": [],
        }
        cmd = _route_by_error_type(state, "broken code", "llm")
        self.assertEqual(cmd.goto, "Coder")
        self.assertEqual(
            cmd.update["verification_result"]["decision_source"], "error_type")

    def test_security_goes_to_planner(self):
        state = {
            "error_type": "security",
            "plan": "dangerous action",
            "locator_suggestions": [],
        }
        cmd = _route_by_error_type(state, "dangerous action", "llm")
        self.assertEqual(cmd.goto, "Planner")
        self.assertEqual(
            cmd.update["verification_result"]["decision_source"], "error_type")

    def test_none_error_type_returns_none(self):
        state = {"error_type": None, "plan": "normal step",
                 "locator_suggestions": []}
        cmd = _route_by_error_type(state, "normal step", "llm")
        self.assertIsNone(cmd)

    def test_cache_source_routes_via_cache_failure(self):
        state = {
            "error_type": "dpcli_ref_stale",
            "plan": "click login",
            "locator_suggestions": [{"url": "https://x.com", "strategies": []}],
            "_cache_hit_id": "cache_123",
            "_failed_code_cache_ids": [],
            "current_url": "https://x.com",
        }
        cmd = _route_by_error_type(state, "click login", "cache")
        self.assertEqual(cmd.goto, "Planner")
        self.assertIn("_cache_failed_this_round", cmd.update)


class TestKeywordFallback(unittest.TestCase):
    """Regex-based keyword fallback should not false-positive."""

    def test_runtime_error_caught(self):
        import re
        log = "Something else\nRuntime Error: foo\nmore text"
        self.assertIsNotNone(
            re.search(r'^\s*(?:Runtime Error|Traceback)', log, re.MULTILINE))

    def test_critical_css_not_caught(self):
        import re
        log = "CriticalCSS loaded successfully"
        self.assertIsNone(
            re.search(r'\bCritical\b.*\bError\b', log))

    def test_element_not_found_caught(self):
        import re
        log = "Error: ElementNotFound in page"
        self.assertIsNotNone(re.search(r'\bElementNotFound\b', log))

    def test_timeout_exception_caught(self):
        import re
        log = "TimeoutException: page load timeout"
        self.assertIsNotNone(re.search(r'\bTimeoutException\b', log))


if __name__ == "__main__":
    unittest.main()
