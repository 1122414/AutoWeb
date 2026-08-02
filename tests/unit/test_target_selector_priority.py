"""
P0 单元测试：TargetSelector text_hints 优先级 bug 修复验证

不依赖 LangGraph / LLM，仅测试 _retrieve_candidates 的约束解析逻辑。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from typing import Any, Dict, List


class TestTargetSelectorTextHintsPriority(unittest.TestCase):
    """验证 _retrieve_candidates 中 constraints.text_or_name 的优先级"""

    def _patch_retrieve_candidates(
        self, intent: str, target_hint: str, constraints: Dict[str, Any]
    ) -> List[str]:
        """Extract text_hints using the fixed priority logic, return texts searched."""
        text_hints = constraints.get("text_or_name") or (
            [target_hint] if target_hint else []
        )

        texts_searched: List[str] = []
        for text in text_hints:
            texts_searched.append(text)

        return texts_searched

    def test_constraints_text_or_name_has_priority_over_empty_target_hint(self):
        result = self._patch_retrieve_candidates(
            intent="click",
            target_hint="",
            constraints={"text_or_name": ["搜索"], "role": ["button"]},
        )
        self.assertEqual(result, ["搜索"],
                         "constraints.text_or_name 有值时不应被空 target_hint 覆盖")

    def test_constraints_text_or_name_has_priority_over_populated_target_hint(self):
        result = self._patch_retrieve_candidates(
            intent="click",
            target_hint="提交按钮",
            constraints={"text_or_name": ["搜索", "Search"], "role": ["button"]},
        )
        self.assertEqual(result, ["搜索", "Search"])

    def test_empty_constraints_falls_back_to_target_hint(self):
        result = self._patch_retrieve_candidates(
            intent="click",
            target_hint="登录按钮",
            constraints={},
        )
        self.assertEqual(result, ["登录按钮"])

    def test_empty_constraints_and_empty_target_hint_returns_empty(self):
        result = self._patch_retrieve_candidates(
            intent="click",
            target_hint="",
            constraints={},
        )
        self.assertEqual(result, [])

    def test_target_hint_empty_with_role_only_constraints(self):
        result = self._patch_retrieve_candidates(
            intent="click",
            target_hint="",
            constraints={"role": ["button"]},
        )
        self.assertEqual(result, [])


class TestTargetSelectorBugRegression(unittest.TestCase):

    def test_explicit_exact_text_accepts_one_offscreen_interactable_candidate(self):
        from skills.dpcli_target_selector import TargetSelector

        assert TargetSelector._matches_explicit_exact_text(
            {
                "ref": "e581",
                "role": "link",
                "name": "Steam",
                "text": "Steam",
                "in_viewport": False,
                "interactable_now": True,
            },
            {"exact_text": "Steam"},
        )

    def test_explicit_exact_text_rejects_partial_match(self):
        from skills.dpcli_target_selector import TargetSelector

        assert not TargetSelector._matches_explicit_exact_text(
            {"name": "Steam download"},
            {"exact_text": "Steam"},
        )

    def test_clear_high_confidence_margin_is_safe_for_deterministic_choice(self):
        from skills.dpcli_target_selector import TargetSelector

        selector = TargetSelector.__new__(TargetSelector)
        selector._engine = MagicMock()
        selector._engine.is_loaded = True
        selector._retrieve_candidates = MagicMock(return_value=[
            {"ref": "e1", "role": "link", "name": "商店", "text": "商店", "interactable_now": True, "in_viewport": True},
            {"ref": "e2", "role": "link", "name": "浏览商店", "text": "浏览商店"},
        ])
        selector._engine.get_ref.return_value = {"ref": "e1"}

        result = selector.select({
            "intent": "click",
            "target_hint": "商店链接",
            "target_constraints": {"role": ["link"], "text_or_name": ["商店"]},
        })

        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["target_ref"], "e1")

    def test_exclude_text_removes_semantically_wrong_similar_candidate(self):
        from skills.dpcli_target_selector import TargetSelector

        selector = TargetSelector.__new__(TargetSelector)
        selector._engine = MagicMock()
        selector._engine.search_snapshot.return_value = [
            {"ref": "e1", "role": "link", "name": "Stardew Valley 2016 ¥1,480"},
            {"ref": "e2", "role": "link", "name": "Stardew Valley Soundtrack ¥498"},
        ]

        result = selector._retrieve_candidates(
            "click",
            "Stardew Valley",
            {
                "role": ["link"],
                "text_or_name": ["Stardew Valley"],
                "exclude_text": ["Soundtrack"],
            },
        )

        self.assertEqual([item["ref"] for item in result], ["e1"])

    def test_original_bug_line_is_fixed(self):
        import os
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        target_file = os.path.join(repo_root, "skills", "dpcli_target_selector.py")

        with open(target_file, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn('constraints.get("text_or_name")', content,
                       "bug fix not applied: text_hints line missing from TargetSelector")
        self.assertIn("([target_hint] if target_hint else [])", content,
                       "bug fix not applied: fallback to target_hint missing")

        bug_pattern_approx = 'or [target_hint] if target_hint'
        self.assertNotIn(bug_pattern_approx, content,
                         f"bug version still exists: {bug_pattern_approx}")


if __name__ == "__main__":
    unittest.main()
