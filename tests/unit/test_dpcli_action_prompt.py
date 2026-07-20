import unittest

import tests.unit.stubs  # noqa: F401 - installs lightweight dependency stubs
from core.nodes import (
    _dpcli_policy_action_from_structured_plan,
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

    def test_validate_rejects_locator_when_full_snapshot_is_on_disk(self):
        state = {
            "dpcli_snapshot_ref": {
                "snapshot_id": "ss_1",
                "index_file": "output/ss_1.index.json",
            },
            "dpcli_snapshot": {
                "data": {
                    "page": {"url": "https://example.test"},
                    "index": {"stats": {"total_nodes": 1000}},
                }
            },
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

    def test_snapshot_plan_does_not_execute_virtual_recoverable_group(self):
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

        self.assertEqual(rewritten, plan)

    def test_extract_target_request_uses_available_data_region_directly(self):
        state = {
            "dpcli_snapshot_ref": {"snapshot_id": "ss_1"},
            "dpcli_agent_view": {
                "capability_map": {
                    "data_regions": [
                        {
                            "ref": "r27",
                            "kind": "card_grid",
                            "name": "hot works",
                            "item_count": 231,
                            "available_actions": ["extract", "list-items"],
                        },
                        {
                            "ref": "r77",
                            "kind": "card_grid",
                            "name": "monthly ticket ranking",
                            "item_count": 200,
                            "available_actions": ["extract", "list-items"],
                        },
                    ]
                }
            },
        }
        plan = {
            "step_intent": "extract",
            "target_request": {
                "required": True,
                "target_hint": "monthly ticket ranking",
                "role": "card_grid",
            },
            "action_payload": {
                "schema": ["title", "author", "url"],
                "limit": 5,
            },
            "reason": "collect ranking books",
        }

        rewritten = _dpcli_snapshot_loop_fallback_plan(state, plan)

        self.assertEqual(rewritten["step_intent"], "extract")
        self.assertEqual(rewritten["target_request"], {"required": False})
        self.assertEqual(rewritten["action_payload"]["target_ref"], "r77")
        self.assertEqual(
            rewritten["action_payload"]["schema"],
            ["title", "author", "url"],
        )
        self.assertEqual(rewritten["action_payload"]["limit"], 5)
        self.assertEqual(rewritten["_planner_rewrite"], "data_region_direct")

    def test_coder_rejects_virtual_group_policy_action(self):
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

        action = _dpcli_policy_action_from_structured_plan(state)

        self.assertIsNone(action)

    def test_ambiguous_data_regions_do_not_bypass_target_selection(self):
        state = {
            "dpcli_snapshot_ref": {"snapshot_id": "ss_1"},
            "dpcli_agent_view": {
                "capability_map": {
                    "data_regions": [
                        {
                            "ref": "r10",
                            "kind": "card_grid",
                            "name": "books",
                            "item_count": 20,
                            "available_actions": ["extract", "list-items"],
                        },
                        {
                            "ref": "r11",
                            "kind": "card_grid",
                            "name": "books",
                            "item_count": 20,
                            "available_actions": ["extract", "list-items"],
                        },
                    ]
                }
            },
        }
        plan = {
            "step_intent": "extract",
            "target_request": {"required": True},
            "reason": "collect books",
        }

        rewritten = _dpcli_snapshot_loop_fallback_plan(state, plan)

        self.assertEqual(rewritten, plan)

    def test_coder_uses_policy_action_for_direct_data_region(self):
        state = {
            "plan": "collect ranking books",
            "execution_mode": "dp_cli",
            "current_url": "https://example.test/rank",
            "dpcli_structured_plan": {
                "step_intent": "extract",
                "_planner_rewrite": "data_region_direct",
                "action_payload": {
                    "target_ref": "r77",
                    "schema": ["title", "url"],
                    "limit": 20,
                },
            },
        }

        command = coder_node(state, {"configurable": {}}, _ExplodingLLM())

        self.assertEqual(command.goto, "Executor")
        self.assertEqual(command.update["_action_source"], "policy")
        self.assertEqual(command.update["generated_action"]["skill"], "extract")
        self.assertEqual(
            command.update["generated_action"]["params"]["target_ref"],
            "r77",
        )


if __name__ == "__main__":
    unittest.main()
