import unittest

import tests.unit.stubs  # noqa: F401 - installs lightweight dependency stubs
from core.nodes import (
    _dpcli_contract_progress_guard,
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

    def test_nested_duplicate_regions_prefer_highest_scoring_specific_region(self):
        shared_samples = [
            {"text": "Box of Chocolate Candy", "url": "/product/1"},
            {"text": "Dark Red Energy Potion", "url": "/product/2"},
        ]
        state = {
            "dpcli_snapshot_ref": {"snapshot_id": "ss_1"},
            "dpcli_agent_view": {
                "capability_map": {
                    "data_regions": [
                        {
                            "ref": "r19",
                            "kind": "card_grid",
                            "name": "Box of Chocolate Candy",
                            "item_count": 5,
                            "source_score": 331,
                            "samples": shared_samples,
                            "available_actions": ["extract"],
                        },
                        {
                            "ref": "r18",
                            "kind": "card_grid",
                            "name": "Box of Chocolate Candy",
                            "item_count": 5,
                            "source_score": 327,
                            "samples": shared_samples,
                            "available_actions": ["extract"],
                        },
                    ]
                }
            },
        }
        plan = {
            "step_intent": "extract",
            "target_request": {
                "required": True,
                "target_hint": "商品卡片区域，包含 Box of Chocolate Candy",
                "role": "region",
                "constraints": {"item_count": 5},
            },
            "action_payload": {"schema": ["title", "price", "url"], "limit": 5},
        }

        rewritten = _dpcli_snapshot_loop_fallback_plan(state, plan)

        self.assertEqual(rewritten["target_request"], {"required": False})
        self.assertEqual(rewritten["action_payload"]["target_ref"], "r19")

    def test_verified_page_progress_overrides_repeated_model_extract(self):
        state = {
            "user_task": "抓取前3页，每页5个商品的名称、价格和URL",
            "current_url": "https://example.test/products",
            "dpcli_task_contract": {
                "version": 2,
                "task": "抓取前3页，每页5个商品的名称、价格和URL",
                "target_url": "https://example.test/products",
                "schema": ["title", "price", "url"],
                "list_schema": ["title", "price", "url"],
                "detail_schema": [],
                "min_items": 15,
                "max_items": 15,
                "per_page_limit": 5,
                "target_pages": 3,
                "collection_mode": "pagination",
                "filter": None,
                "detail_required": False,
            },
            "dpcli_task_progress": {
                "items": [
                    {"title": f"p{i}", "price": i, "url": f"/p/{i}"}
                    for i in range(5)
                ],
                "completed_pages": [1],
                "active_page": 1,
                "failed_region_refs": ["r19"],
            },
            "dpcli_agent_view": {
                "capability_map": {
                    "pagination": {
                        "controls": [
                            {"ref": "e25", "label": "1", "direction": "page_number", "enabled": True},
                            {"ref": "e26", "label": "2", "direction": "page_number", "enabled": True},
                        ]
                    }
                }
            },
        }
        model_plan = {
            "step_intent": "extract",
            "target_request": {"required": False},
            "action_payload": {"target_ref": "r19"},
        }

        guarded = _dpcli_contract_progress_guard(state, model_plan)

        self.assertEqual(guarded["step_intent"], "click")
        self.assertEqual(guarded["action_payload"]["ref"], "e26")
        self.assertEqual(guarded["_planner_rewrite"], "contract_progress_guard")

    def test_contract_filter_obligation_overrides_untracked_model_type(self):
        from skills.task_lifecycle import task_lifecycle

        task = "打开 https://example.test，在搜索框筛选关键词“a”，抓取前2页"
        state = {
            "user_task": task,
            "current_url": "https://example.test",
            "dpcli_task_contract": task_lifecycle.compile(task),
            "dpcli_task_progress": {},
            "dpcli_agent_view": {
                "capability_map": {
                    "search": {
                        "input_ref": "e7",
                        "input_name": "search",
                        "kind": "search_area",
                    }
                }
            },
        }
        model_plan = {
            "step_intent": "type",
            "target_request": {"required": True},
            "action_payload": {"text": "a"},
        }

        guarded = _dpcli_contract_progress_guard(state, model_plan)

        self.assertEqual(guarded["step_intent"], "type")
        self.assertEqual(guarded["action_payload"]["ref"], "e7")
        self.assertEqual(guarded["action_payload"]["filter_stage"], "applied")
        self.assertTrue(guarded["_contract_action"])

        state["dpcli_task_progress"] = {
            "applied_filter_indices": [0],
            "filter_applied": True,
            "items": [],
            "completed_pages": [],
            "active_page": 1,
            "failed_region_refs": [],
        }
        state["dpcli_agent_view"]["capability_map"]["data_regions"] = [
            {
                "ref": "r20",
                "kind": "table",
                "name": "teams",
                "item_count": 10,
                "available_actions": ["extract"],
                "source_score": 1000,
            }
        ]

        post_filter = _dpcli_contract_progress_guard(state, model_plan)

        self.assertEqual(post_filter["step_intent"], "extract")
        self.assertEqual(post_filter["action_payload"]["target_ref"], "r20")

    def test_recoverable_extract_caps_limit_to_global_remaining_count(self):
        state = {
            "user_task": "提取20条名言的正文和作者",
            "dpcli_snapshot_ref": {"snapshot_id": "ss_2"},
            "dpcli_task_contract": {
                "schema": ["text", "author"],
                "list_schema": ["text", "author"],
                "min_items": 20,
                "max_items": 20,
                "per_page_limit": 20,
            },
            "dpcli_task_progress": {
                "items": [
                    {"text": f"quote {i}", "author": "author"}
                    for i in range(10)
                ]
            },
            "dpcli_agent_view": {
                "capability_map": {
                    "data_regions": [
                        {
                            "ref": "r10",
                            "kind": "repeated_structure",
                            "name": "quotes",
                            "item_count": 30,
                            "available_actions": ["extract"],
                        }
                    ]
                }
            },
        }
        model_plan = {
            "step_intent": "extract",
            "target_request": {"required": True, "target_hint": "quotes"},
            "action_payload": {"schema": ["text", "author"], "limit": 20},
        }

        rewritten = _dpcli_snapshot_loop_fallback_plan(state, model_plan)

        self.assertEqual(rewritten["action_payload"]["limit"], 10)

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
