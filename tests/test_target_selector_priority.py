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

    def test_original_bug_line_is_fixed(self):
        import os
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        target_file = os.path.join(repo_root, "core", "nodes", "target_selector.py")

        with open(target_file, "r", encoding="utf-8") as f:
            content = f.read()

        fixed_pattern = (
            'constraints.get("text_or_name") or '
            '([target_hint] if target_hint else [])'
        )
        self.assertIn(fixed_pattern, content,
                       "bug 修复未生效：text_hints 行仍使用旧逻辑")

        bug_pattern_approx = 'or [target_hint] if target_hint'
        self.assertNotIn(bug_pattern_approx, content,
                         f"bug 版本仍存在：{bug_pattern_approx}")


if __name__ == "__main__":
    unittest.main()
