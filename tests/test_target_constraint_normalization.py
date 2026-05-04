"""
P0: TargetSelector constraint mapping fix - _normalize_target_constraints unit tests.

Validates that planner one-level fields (role, text_or_name, region_hint, near)
are correctly merged into the constraints dict consumed by TargetSelector.select().
"""
from __future__ import annotations

import importlib.util
import os
import unittest


def _load_normalizer():
    """Load _normalize_target_constraints from target_selector.py directly,
    bypassing core.nodes.__init__ to avoid heavy dependency chain."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "target_selector_module",
        os.path.join(repo_root, "core", "nodes", "target_selector.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._normalize_target_constraints


_normalize_target_constraints = _load_normalizer()


class TestNormalizeTargetConstraints(unittest.TestCase):

    def test_role_and_text_or_name_merged_into_constraints(self):
        target_request = {
            "required": True,
            "target_hint": "search btn",
            "role": "button",
            "text_or_name": ["search"],
            "region_hint": "search_area",
            "constraints": {},
        }
        result = _normalize_target_constraints(target_request)
        self.assertEqual(result["role"], ["button"])
        self.assertEqual(result["text_or_name"], ["search"])
        self.assertEqual(result["region_hint"], "search_area")

    def test_role_as_list_preserved(self):
        target_request = {
            "role": ["button", "submit"],
            "constraints": {},
        }
        result = _normalize_target_constraints(target_request)
        self.assertEqual(result["role"], ["button", "submit"])

    def test_existing_constraints_not_overwritten(self):
        target_request = {
            "role": "button",
            "text_or_name": ["search"],
            "constraints": {"role": ["link"], "text_or_name": ["login"]},
        }
        result = _normalize_target_constraints(target_request)
        self.assertEqual(result["role"], ["link"])
        self.assertEqual(result["text_or_name"], ["login"])

    def test_near_merged_when_not_in_constraints(self):
        target_request = {
            "near": "search box",
            "constraints": {},
        }
        result = _normalize_target_constraints(target_request)
        self.assertEqual(result["near"], "search box")

    def test_empty_target_request_returns_empty_constraints(self):
        result = _normalize_target_constraints({})
        self.assertEqual(result, {})

    def test_no_first_level_fields_passes_constraints_through(self):
        target_request = {
            "constraints": {"role": ["button"], "text_or_name": ["search"]},
        }
        result = _normalize_target_constraints(target_request)
        self.assertEqual(result, {"role": ["button"], "text_or_name": ["search"]})

    def test_all_fields_merge_without_collision(self):
        target_request = {
            "required": True,
            "target_hint": "search btn",
            "role": "button",
            "text_or_name": ["search"],
            "region_hint": "search_area",
            "near": "search box",
            "constraints": {"interactable_now": True},
        }
        result = _normalize_target_constraints(target_request)
        self.assertEqual(result["role"], ["button"])
        self.assertEqual(result["text_or_name"], ["search"])
        self.assertEqual(result["region_hint"], "search_area")
        self.assertEqual(result["near"], "search box")
        self.assertEqual(result["interactable_now"], True)

    def test_normalizer_semantic_equivalence(self):
        """Verify the normalizer produces the same result as manual merge."""
        target_request = {
            "required": True,
            "target_hint": "search btn",
            "role": "button",
            "text_or_name": ["search"],
            "region_hint": "search_area",
            "constraints": {},
        }
        result = _normalize_target_constraints(target_request)
        self.assertEqual(result.get("role"), ["button"])
        self.assertEqual(result.get("text_or_name"), ["search"])
        self.assertEqual(result.get("region_hint"), "search_area")


if __name__ == "__main__":
    unittest.main()
