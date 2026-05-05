import unittest

import tests.unit.stubs  # noqa: F401 - installs lightweight dependency stubs
from core.nodes import (
    _dpcli_snapshot_loop_fallback_plan,
    _extract_json_object,
    _validate_dpcli_action,
    coder_node,
)


class _Response:
    def __init__(self, content):
        self.content = content


class _LLM:
    def __init__(self, content):
        self.content = content

    def invoke(self, _messages):
        return _Response(self.content)


class _ExplodingLLM:
    def invoke(self, _messages):
        raise AssertionError("policy action should not call LLM")


class DPCLIActionPromptTests(unittest.TestCase):
    def test_extract_json_object_from_fenced_json(self):
        parsed = _extract_json_object('```json\n{"skill":"click","params":{"ref":"e1"}}\n```')

        self.assertEqual(parsed["skill"], "click")
        self.assertEqual(parsed["params"]["ref"], "e1")

    def test_validate_rejects_click_without_target(self):
        self.assertEqual(
            _validate_dpcli_action({"skill": "click", "params": {}}),
            "click requires ref or locator",
        )

    def test_validate_rejects_locator_when_snapshot_refs_exist(self):
        state = {
            "dpcli_snapshot": {
                "data": {
                    "index": {
                        "interactable_elements": [{"ref": "e1"}],
                    }
                }
            }
        }

        self.assertEqual(
            _validate_dpcli_action(
                {"skill": "click", "params": {"locator": "a.book"}},
                state,
            ),
            "click must use a snapshot ref instead of a free-form locator",
        )

    def test_coder_outputs_dpcli_action(self):
        state = {
            "plan": "点击搜索按钮",
            "execution_mode": "dp_cli",
            "current_url": "https://example.test",
            "dpcli_snapshot": {
                "data": {
                    "page": {"url": "https://example.test"},
                    "index": {"interactable_elements": [{"ref": "e1", "role": "button", "name": "Search"}]},
                }
            },
        }
        llm = _LLM('{"skill":"click","params":{"ref":"e1"},"reason":"search"}')

        command = coder_node(state, {"configurable": {}}, llm)

        self.assertEqual(command.goto, "Executor")
        self.assertEqual(command.update["execution_mode"], "dp_cli")
        self.assertEqual(command.update["generated_action"]["skill"], "click")
        self.assertIsNone(command.update["generated_code"])

    def test_invalid_action_retries_coder(self):
        state = {"plan": "点击搜索按钮", "execution_mode": "dp_cli", "coder_retry_count": 0}
        llm = _LLM('{"skill":"click","params":{},"reason":"bad"}')

        command = coder_node(state, {"configurable": {}}, llm)

        self.assertEqual(command.goto, "Coder")
        self.assertEqual(command.update["coder_retry_count"], 1)
        self.assertEqual(command.update["error_type"], "dpcli_action_json")

    def test_snapshot_plan_rewrites_to_recoverable_group(self):
        state = {
            "dpcli_snapshot_ref": {"snapshot_id": "ss_1"},
            "dpcli_agent_view": {
                "coverage": {
                    "recoverable_groups": [
                        {"group_ref": "g_rank_links", "count": 30}
                    ]
                }
            },
        }
        plan = {
            "step_intent": "snapshot",
            "target_request": {"required": False},
            "reason": "need more context",
        }

        rewritten = _dpcli_snapshot_loop_fallback_plan(state, plan)

        self.assertEqual(rewritten["step_intent"], "list-items")
        self.assertEqual(rewritten["action_payload"]["group_ref"], "g_rank_links")
        self.assertEqual(rewritten["_planner_rewrite"], "snapshot_loop_guard")

    def test_coder_uses_policy_action_for_rewritten_snapshot_loop(self):
        state = {
            "plan": "collect ranking items",
            "execution_mode": "dp_cli",
            "current_url": "https://example.test/rank",
            "dpcli_structured_plan": {
                "step_intent": "list-items",
                "_planner_rewrite": "snapshot_loop_guard",
                "action_payload": {"group_ref": "g_rank_links", "sample_size": 10},
            },
        }

        command = coder_node(state, {"configurable": {}}, _ExplodingLLM())

        self.assertEqual(command.goto, "Executor")
        self.assertEqual(command.update["_action_source"], "policy")
        self.assertEqual(command.update["generated_action"]["skill"], "list-items")
        self.assertEqual(
            command.update["generated_action"]["params"]["group_ref"],
            "g_rank_links",
        )


if __name__ == "__main__":
    unittest.main()
