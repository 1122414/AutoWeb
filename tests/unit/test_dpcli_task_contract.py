from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import tests.unit.stubs  # noqa: F401 - installs lightweight dependency stubs
from core.nodes._dpcli import _dpcli_policy_action_from_structured_plan
from core.nodes._dpcli import _should_use_dpcli_action
from core.nodes.planner import _dpcli_contract_planner_step
from core.nodes.verifier import (
    _advance_contract_page_progress,
    _contract_action_verification,
    _merge_dpcli_contract_progress,
    _verify_dpcli_action_with_signals,
    verifier_node,
)
from skills.dpcli_task_contract import (
    build_contract_plan,
    build_task_contract,
    evaluate_contract_items,
    merge_contract_progress,
)
from skills.dpcli_crawl_policy import (
    build_detail_batch_action,
    goal_requests_detail_batch,
    should_run_detail_batch,
)
from skills.dpcli_result_enricher import enrich_extract_result


class TaskContractParsingTests(unittest.TestCase):
    def test_books_contract_preserves_exact_count_and_fields(self):
        contract = build_task_contract(
            "打开 https://books.toscrape.com/，只提取当前第一页前5本书的"
            "标题和对应URL，得到5条有效数据后立即结束任务。"
        )

        self.assertEqual(contract["target_url"], "https://books.toscrape.com/")
        self.assertEqual(contract["schema"], ["title", "url"])
        self.assertEqual(contract["min_items"], 5)
        self.assertEqual(contract["max_items"], 5)
        self.assertEqual(contract["per_page_limit"], 5)
        self.assertEqual(contract["target_pages"], 1)
        self.assertFalse(contract["detail_required"])

    def test_product_pagination_contract_accumulates_two_pages(self):
        contract = build_task_contract(
            "打开 https://web-scraping.dev/products，提取当前第一页5个商品的"
            "名称、价格和对应URL，然后进入第2页再提取5个商品；"
            "合计至少10条数据后结束任务。"
        )

        self.assertEqual(contract["schema"], ["title", "price", "url"])
        self.assertEqual(contract["min_items"], 10)
        self.assertEqual(contract["max_items"], 10)
        self.assertEqual(contract["per_page_limit"], 5)
        self.assertEqual(contract["target_pages"], 2)

    def test_three_page_contract_uses_pagination_collection_mode(self):
        contract = build_task_contract(
            "打开 https://example.test/products，连续抓取前3页，每页5个商品的"
            "名称、价格和URL，总计15条；中断后从已完成页继续。"
        )

        self.assertEqual(contract["target_pages"], 3)
        self.assertEqual(contract["min_items"], 15)
        self.assertEqual(contract["per_page_limit"], 5)
        self.assertEqual(contract["collection_mode"], "pagination")
        self.assertTrue(contract["recovery"]["enabled"])

    def test_infinite_scroll_contract_preserves_bounded_scroll_policy(self):
        contract = build_task_contract(
            "打开 https://example.test/feed，持续向下滚动加载，最多滚动4轮，"
            "提取20篇文章的标题和URL，达到20条后结束。"
        )

        self.assertEqual(contract["collection_mode"], "infinite_scroll")
        self.assertEqual(contract["max_scroll_rounds"], 4)
        self.assertEqual(contract["min_items"], 20)
        self.assertEqual(contract["target_pages"], 1)

    def test_text_filter_contract_extracts_keyword_and_submit_semantics(self):
        contract = build_task_contract(
            "打开 https://example.test/teams，在搜索框筛选关键词“Boston”，"
            "提交筛选后抓取前2页，每页10行球队名称和年份。"
        )

        self.assertEqual(contract["target_pages"], 2)
        self.assertEqual(
            contract["filter"],
            {
                "kind": "text",
                "value": "Boston",
                "field_hint": "搜索框",
                "submit": True,
            },
        )

    def test_common_chinese_content_units_preserve_requested_count(self):
        tasks = (
            "打开 https://example.test/movies，提取前5部电影的名称和URL。",
            "打开 https://example.test/news，提取前5篇新闻的标题和URL。",
            "打开 https://example.test/poems，提取前5首诗的标题和URL。",
            "打开 https://example.test/recipes，提取前5道菜谱的名称和URL。",
        )

        for task in tasks:
            with self.subTest(task=task):
                contract = build_task_contract(task)
                self.assertEqual(contract["min_items"], 5)
                self.assertEqual(contract["max_items"], 5)
                self.assertEqual(contract["per_page_limit"], 5)

    def test_quote_and_table_field_aliases_are_canonical(self):
        quotes = build_task_contract(
            "打开 https://quotes.toscrape.com/，提取10条名言的正文、作者和标签。"
        )
        hockey = build_task_contract(
            "打开 https://example.test，提取25行球队数据，字段包括球队名称、"
            "年份、胜场和负场。"
        )

        self.assertEqual(quotes["schema"], ["text", "author", "tags"])
        self.assertEqual(hockey["schema"], ["team", "year", "wins", "losses"])

    def test_detail_link_and_negation_do_not_trigger_detail_batch(self):
        link_only = build_task_contract(
            "提取书名和详情链接，不要进入详情页，也不要提取详情信息。"
        )
        actual_detail = build_task_contract(
            "提取书名后进入每本书详情页，获取简介和描述。"
        )

        self.assertFalse(link_only["detail_required"])
        self.assertTrue(actual_detail["detail_required"])
        self.assertFalse(goal_requests_detail_batch("提取书名和详情链接。"))
        self.assertFalse(
            should_run_detail_batch(
                {
                    "user_task": "提取书名和详情链接，不要进入详情页。",
                    "dpcli_task_contract": link_only,
                    "dpcli_result": {
                        "ok": True,
                        "action": "extract",
                        "data": {
                            "items": [
                                {
                                    "title": "One",
                                    "url": "https://example.test/one",
                                }
                            ]
                        },
                    },
                }
            )
        )


class TaskContractPlanningTests(unittest.TestCase):
    def test_explicit_dpcli_mode_does_not_depend_on_global_feature_flag(self):
        self.assertTrue(_should_use_dpcli_action({"execution_mode": "dp_cli"}))

    def test_contract_planner_routes_blank_page_to_coder_without_llm(self):
        command = _dpcli_contract_planner_step(
            {
                "user_task": (
                    "打开 https://books.toscrape.com/，"
                    "提取前5本书的标题和URL。"
                ),
                "current_url": "chrome://newtab/",
                "dpcli_task_progress": {},
            },
            loop_count=0,
            verification={},
        )

        self.assertEqual(command.goto, "Coder")
        self.assertEqual(
            command.update["dpcli_structured_plan"]["step_intent"],
            "open",
        )
        self.assertEqual(command.update["execution_mode"], "dp_cli")

    def test_contract_policy_actions_cover_open_click_wait_and_extract(self):
        cases = [
            (
                {"step_intent": "open", "action_payload": {"url": "https://example.test"}},
                {"skill": "open", "params": {"url": "https://example.test"}},
            ),
            (
                {"step_intent": "click", "action_payload": {"ref": "e26"}},
                {"skill": "click", "params": {"ref": "e26"}},
            ),
            (
                {"step_intent": "wait", "action_payload": {"seconds": 1.25}},
                {"skill": "wait", "params": {"seconds": 1.25}},
            ),
            (
                {
                    "step_intent": "extract",
                    "action_payload": {
                        "target_ref": "r9",
                        "schema": ["text", "author"],
                        "limit": 10,
                    },
                },
                {
                    "skill": "extract",
                    "params": {
                        "target_ref": "r9",
                        "schema": ["text", "author"],
                        "limit": 10,
                    },
                },
            ),
        ]

        for structured_plan, expected in cases:
            structured_plan["_contract_action"] = True
            with self.subTest(intent=structured_plan["step_intent"]):
                action = _dpcli_policy_action_from_structured_plan(
                    {"dpcli_structured_plan": structured_plan}
                )
                self.assertEqual(action["skill"], expected["skill"])
                self.assertEqual(action["params"], expected["params"])

    def test_blank_page_uses_deterministic_open_without_llm(self):
        contract = build_task_contract(
            "打开 https://books.toscrape.com/，提取5本书的标题和URL。"
        )
        plan, updates = build_contract_plan(
            {
                "current_url": "chrome://newtab/",
                "dpcli_agent_view": {},
                "dpcli_task_progress": {},
            },
            contract,
        )

        self.assertEqual(plan["step_intent"], "open")
        self.assertEqual(
            plan["action_payload"],
            {"url": "https://books.toscrape.com/"},
        )
        self.assertTrue(plan["_contract_action"])
        self.assertEqual(updates["dpcli_task_contract"], contract)

    def test_extract_plan_keeps_contract_schema_and_limit(self):
        contract = build_task_contract(
            "打开 https://books.toscrape.com/，只提取第一页前5本书的标题和URL。"
        )
        plan, _updates = build_contract_plan(
            {
                "current_url": "https://books.toscrape.com/",
                "dpcli_agent_view": {
                    "page_identity": {"url": "https://books.toscrape.com/"},
                    "capability_map": {
                        "data_regions": [
                            {
                                "ref": "r8",
                                "kind": "repeated_structure",
                                "item_count": 41,
                                "source_score": 323,
                                "samples": [{"text": "Home", "url": "/index.html"}],
                                "available_actions": ["extract"],
                            },
                            {
                                "ref": "r79",
                                "kind": "list",
                                "item_count": 20,
                                "source_score": 458,
                                "samples": [
                                    {
                                        "text": "A Light in the Attic",
                                        "url": "/catalogue/a-light/index.html",
                                    }
                                ],
                                "available_actions": ["extract"],
                            },
                        ],
                        "pagination": [],
                    },
                },
                "dpcli_task_progress": {},
            },
            contract,
        )

        self.assertEqual(plan["step_intent"], "extract")
        self.assertEqual(plan["action_payload"]["target_ref"], "r79")
        self.assertEqual(plan["action_payload"]["schema"], ["title", "url"])
        self.assertEqual(plan["action_payload"]["limit"], 5)

    def test_exhausted_contract_regions_end_as_controlled_failure(self):
        task = "打开 https://example.test/movies，提取前5部电影的标题和URL。"
        contract = build_task_contract(task)
        state = {
            "user_task": task,
            "current_url": "https://example.test/movies",
            "dpcli_task_contract": contract,
            "dpcli_agent_view": {
                "page_identity": {"url": "https://example.test/movies"},
                "capability_map": {
                    "data_regions": [
                        {
                            "ref": "r8",
                            "kind": "card_grid",
                            "item_count": 3,
                            "source_score": 300,
                            "samples": [],
                            "available_actions": ["extract"],
                        }
                    ],
                    "pagination": [],
                },
            },
            "dpcli_task_progress": {
                "items": [
                    {
                        "title": f"电影 {index}",
                        "url": f"https://example.test/movies/{index}",
                    }
                    for index in range(3)
                ],
                "active_page": 1,
                "failed_region_refs": ["r8"],
            },
        }

        plan, _updates = build_contract_plan(state, contract)
        command = _dpcli_contract_planner_step(
            state,
            loop_count=5,
            verification={},
        )

        self.assertEqual(plan["step_intent"], "fail")
        self.assertEqual(command.goto, "__end__")
        self.assertTrue(command.update["is_complete"])
        self.assertFalse(command.update["verification_result"]["is_success"])
        self.assertIn("3/5", command.update["verification_result"]["summary"])

    def test_raw_snapshot_quote_region_avoids_llm_fallback(self):
        contract = build_task_contract(
            "从 https://example.test 提取2条名言的正文、作者和标签。"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            index_file = Path(temp_dir) / "snapshot.index.json"
            index_file.write_text(
                json.dumps(
                    {
                        "by_ref": {
                            "r2": {
                                "ref": "r2",
                                "tag": "div",
                                "text": (
                                    "“One” by A Tags: x "
                                    "“Two” by B Tags: y "
                                    "“Three” by C Tags: z"
                                ),
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            plan, _updates = build_contract_plan(
                {
                    "current_url": "https://example.test",
                    "dpcli_agent_view": {"capability_map": {}},
                    "dpcli_snapshot_ref": {"index_file": str(index_file)},
                    "dpcli_task_progress": {},
                },
                contract,
            )

        self.assertEqual(plan["step_intent"], "extract")
        self.assertEqual(plan["action_payload"]["target_ref"], "r2")

    def test_second_page_uses_concrete_pagination_element_ref(self):
        contract = build_task_contract(
            "打开 https://web-scraping.dev/products，第一页提取5个商品，"
            "进入第2页再提取5个，字段为名称、价格和URL，合计10条。"
        )
        progress = {
            "items": [
                {"title": f"Product {index}", "price": "4.99", "url": f"/p/{index}"}
                for index in range(5)
            ],
            "completed_pages": [1],
            "active_page": 1,
        }
        plan, updates = build_contract_plan(
            {
                "current_url": "https://web-scraping.dev/products",
                "dpcli_agent_view": {
                    "page_identity": {
                        "url": "https://web-scraping.dev/products"
                    },
                    "capability_map": {
                        "data_regions": [],
                        "pagination": [
                            {
                                "group_id": "g_pagination_virtual",
                                "controls": [
                                    {
                                        "ref": "e25",
                                        "label": "1",
                                        "direction": "page_number",
                                        "enabled": True,
                                    },
                                    {
                                        "ref": "e26",
                                        "label": "2",
                                        "direction": "page_number",
                                        "enabled": True,
                                    },
                                ],
                            }
                        ],
                    },
                },
                "dpcli_task_progress": progress,
            },
            contract,
        )

        self.assertEqual(plan["step_intent"], "click")
        self.assertEqual(
            plan["action_payload"],
            {"ref": "e26", "page_number": 2},
        )
        self.assertEqual(updates["dpcli_task_progress"]["active_page"], 1)

    def test_filter_is_applied_before_list_extraction(self):
        contract = build_task_contract(
            "打开 https://example.test/teams，在搜索框筛选关键词“Boston”，"
            "提交筛选后提取10行球队名称和年份。"
        )
        plan, updates = build_contract_plan(
            {
                "current_url": "https://example.test/teams",
                "dpcli_agent_view": {
                    "capability_map": {
                        "search": [
                            {
                                "input_ref": "e10",
                                "input_name": "Search",
                                "nearby_buttons": ["e11"],
                            }
                        ],
                        "forms": [],
                        "data_regions": [
                            {
                                "ref": "r2",
                                "kind": "table",
                                "item_count": 10,
                                "available_actions": ["extract"],
                            }
                        ],
                        "pagination": [],
                    }
                },
                "dpcli_task_progress": {},
            },
            contract,
        )

        self.assertEqual(plan["step_intent"], "type")
        self.assertEqual(
            plan["action_payload"],
            {
                "ref": "e10",
                "text": "Boston",
                "submit": True,
                "filter_stage": "applied",
            },
        )
        self.assertFalse(updates["dpcli_task_progress"]["filter_applied"])

    def test_infinite_scroll_runs_after_current_region_is_consumed(self):
        contract = build_task_contract(
            "打开 https://example.test/feed，持续向下滚动加载，最多滚动4轮，"
            "提取20篇文章的标题和URL。"
        )
        progress = {
            "items": [
                {"title": f"Article {index}", "url": f"https://example.test/{index}"}
                for index in range(10)
            ],
            "completed_pages": [1],
            "active_page": 1,
            "failed_region_refs": ["r2"],
            "scroll_round": 0,
        }
        plan, updates = build_contract_plan(
            {
                "current_url": "https://example.test/feed",
                "dpcli_agent_view": {
                    "capability_map": {
                        "data_regions": [
                            {
                                "ref": "r2",
                                "kind": "list",
                                "item_count": 10,
                                "available_actions": ["extract"],
                            }
                        ],
                        "pagination": [],
                    }
                },
                "dpcli_task_progress": progress,
            },
            contract,
        )

        self.assertEqual(plan["step_intent"], "scroll")
        self.assertEqual(plan["action_payload"]["direction"], "down")
        self.assertEqual(plan["action_payload"]["round"], 1)
        self.assertEqual(updates["dpcli_task_progress"]["scroll_round"], 0)

    def test_contract_type_and_scroll_plans_map_to_executable_actions(self):
        type_action = _dpcli_policy_action_from_structured_plan(
            {
                "dpcli_structured_plan": {
                    "step_intent": "type",
                    "action_payload": {
                        "ref": "e10",
                        "text": "Boston",
                        "submit": True,
                    },
                    "_contract_action": True,
                }
            }
        )
        scroll_action = _dpcli_policy_action_from_structured_plan(
            {
                "dpcli_structured_plan": {
                    "step_intent": "scroll",
                    "action_payload": {
                        "direction": "down",
                        "amount": 900,
                        "to": "bottom",
                        "wait_time": 1.0,
                    },
                    "_contract_action": True,
                }
            }
        )

        self.assertEqual(
            type_action,
            {
                "skill": "type",
                "params": {
                    "text": "Boston",
                    "ref": "e10",
                    "submit": True,
                },
                "reason": "deterministic dp_cli plan",
            },
        )
        self.assertEqual(scroll_action["skill"], "scroll")
        self.assertEqual(scroll_action["params"]["to"], "bottom")

    def test_finish_plan_is_deterministic_when_contract_is_satisfied(self):
        contract = build_task_contract(
            "从 https://example.test 提取2条标题和URL。"
        )
        progress = {
            "items": [
                {"title": "One", "url": "https://example.test/1"},
                {"title": "Two", "url": "https://example.test/2"},
            ],
            "completed_pages": [1],
            "active_page": 1,
        }
        plan, _updates = build_contract_plan(
            {
                "current_url": "https://example.test",
                "dpcli_agent_view": {"capability_map": {}},
                "dpcli_task_progress": progress,
            },
            contract,
        )

        self.assertEqual(plan["step_intent"], "finish")
        self.assertTrue(plan["_contract_action"])


class TaskContractVerificationTests(unittest.TestCase):
    def _snapshot_state(self, contract, nodes):
        temp_dir = tempfile.TemporaryDirectory()
        index_file = Path(temp_dir.name) / "snapshot.index.json"
        index_file.write_text(
            json.dumps(
                {"by_ref": {node["ref"]: node for node in nodes}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.addCleanup(temp_dir.cleanup)
        return {
            "dpcli_task_contract": contract,
            "dpcli_snapshot_ref": {"index_file": str(index_file)},
        }

    def test_snapshot_enricher_projects_quote_blocks(self):
        contract = build_task_contract(
            "从 https://example.test 提取2条名言的正文、作者和标签。"
        )
        nodes = [
            {"ref": "r10", "tag": "div", "text": "“First quote.” by One"},
            {"ref": "r11", "tag": "span", "text": "“First quote.”"},
            {"ref": "r12", "tag": "span", "text": "by One (about)"},
            {"ref": "r13", "tag": "small", "text": "One"},
            {"ref": "r14", "tag": "div", "text": "Tags: alpha beta"},
            {"ref": "r15", "tag": "div", "text": "“Second quote.” by Two"},
            {"ref": "r16", "tag": "span", "text": "“Second quote.”"},
            {"ref": "r17", "tag": "span", "text": "by Two (about)"},
            {"ref": "r18", "tag": "small", "text": "Two"},
            {"ref": "r19", "tag": "div", "text": "Tags: gamma"},
        ]
        state = self._snapshot_state(contract, nodes)
        action = {
            "skill": "extract",
            "params": {"target_ref": "r2", "schema": contract["schema"], "limit": 2},
        }
        result = {
            "ok": True,
            "action": "extract",
            "data": {"items": [{"text": "Next"}]},
        }

        enriched = enrich_extract_result(state, action, result)

        self.assertEqual(enriched["data"]["item_count"], 2)
        self.assertEqual(enriched["data"]["items"][0]["author"], "One")
        self.assertEqual(enriched["data"]["items"][1]["tags"], ["gamma"])

    def test_snapshot_enricher_adds_product_prices(self):
        contract = build_task_contract(
            "从 https://example.test/products 提取2个商品的名称、价格和URL。"
        )
        nodes = [
            {"ref": "r20", "tag": "h3", "text": "Product One"},
            {"ref": "r21", "tag": "p", "text": "Description"},
            {"ref": "r22", "tag": "div", "text": "24.99"},
            {"ref": "r30", "tag": "h3", "text": "Product Two"},
            {"ref": "r31", "tag": "div", "text": "4.99"},
        ]
        state = self._snapshot_state(contract, nodes)
        action = {
            "skill": "extract",
            "params": {"target_ref": "r19", "schema": contract["schema"], "limit": 2},
        }
        result = {
            "ok": True,
            "action": "extract",
            "data": {
                "items": [
                    {"title": "Product One", "url": "https://example.test/1"},
                    {"title": "Product Two", "url": "https://example.test/2"},
                ]
            },
        }

        enriched = enrich_extract_result(state, action, result)

        self.assertEqual(
            [item["price"] for item in enriched["data"]["items"]],
            ["24.99", "4.99"],
        )

    def test_snapshot_enricher_projects_table_rows(self):
        contract = build_task_contract(
            "从 https://example.test 提取2行球队名称、年份、胜场和负场。"
        )
        nodes = [
            {"ref": "r38", "tag": "tr", "role": "row", "text": "headers"},
            {"ref": "r39", "tag": "th", "text": "Team Name"},
            {"ref": "r48", "tag": "tr", "role": "row", "text": "Boston 1990 44 24"},
            {"ref": "r49", "tag": "td", "text": "Boston Bruins"},
            {"ref": "r50", "tag": "td", "text": "1990"},
            {"ref": "r51", "tag": "td", "text": "44"},
            {"ref": "r52", "tag": "td", "text": "24"},
            {"ref": "r58", "tag": "tr", "role": "row", "text": "Buffalo 1990 31 30"},
            {"ref": "r59", "tag": "td", "text": "Buffalo Sabres"},
            {"ref": "r60", "tag": "td", "text": "1990"},
            {"ref": "r61", "tag": "td", "text": "31"},
            {"ref": "r62", "tag": "td", "text": "30"},
        ]
        state = self._snapshot_state(contract, nodes)
        action = {
            "skill": "extract",
            "params": {"target_ref": "r36", "schema": contract["schema"], "limit": 2},
        }
        result = {"ok": True, "action": "extract", "data": {"items": []}}

        enriched = enrich_extract_result(state, action, result)

        self.assertEqual(enriched["data"]["item_count"], 2)
        self.assertEqual(enriched["data"]["items"][0]["team"], "Boston Bruins")
        self.assertEqual(enriched["data"]["items"][1]["losses"], "30")

    def test_action_verifier_uses_original_contract_not_generated_schema(self):
        contract = build_task_contract(
            "从 https://example.test 提取3行球队名称、年份、胜场和负场。"
        )
        state = {
            "dpcli_task_contract": contract,
            "generated_action": {
                "skill": "extract",
                "params": {
                    "target_ref": "r5",
                    "schema": ["title", "url"],
                    "limit": 3,
                },
            },
            "dpcli_result": {
                "ok": True,
                "data": {
                    "items": [
                        {"title": "Sandbox", "url": "https://example.test/pages"},
                        {"title": "Lessons", "url": "https://example.test/lessons"},
                        {"title": "FAQ", "url": "https://example.test/faq"},
                    ]
                },
            },
        }

        result = _contract_action_verification(state, "extract")

        self.assertFalse(result["is_success"])
        self.assertEqual(result["decision_source"], "task_contract")

    def test_contract_progress_marks_full_task_done(self):
        contract = build_task_contract(
            "从 https://example.test 提取2条标题和URL。"
        )
        merged, evaluation, is_done = _merge_dpcli_contract_progress(
            {
                "dpcli_task_contract": contract,
                "dpcli_task_progress": {"active_page": 1},
                "dpcli_result": {
                    "ok": True,
                    "data": {
                        "items": [
                            {"title": "One", "url": "https://example.test/1"},
                            {"title": "Two", "url": "https://example.test/2"},
                        ]
                    },
                },
            }
        )

        self.assertTrue(is_done)
        self.assertTrue(evaluation["is_success"])
        self.assertEqual(merged["completed_pages"], [1])

    def test_partial_valid_region_is_accepted_for_cumulative_progress(self):
        contract = build_task_contract(
            "从 https://example.test 提取5条标题和URL。"
        )
        state = {
            "dpcli_task_contract": contract,
            "generated_action": {
                "skill": "extract",
                "params": {
                    "target_ref": "r2",
                    "schema": ["title", "url"],
                    "limit": 5,
                },
            },
            "dpcli_result": {
                "ok": True,
                "action": "extract",
                "data": {
                    "items": [
                        {"title": f"Item {index}", "url": f"https://example.test/{index}"}
                        for index in range(3)
                    ]
                },
            },
        }

        result = _contract_action_verification(state, "extract")
        merged, evaluation, is_done = _merge_dpcli_contract_progress(state)

        self.assertTrue(result["is_success"])
        self.assertIn("partial", result["summary"])
        self.assertEqual(len(merged["items"]), 3)
        self.assertFalse(evaluation["is_success"])
        self.assertFalse(is_done)

    def test_verifier_accumulates_multiple_regions_on_same_page(self):
        contract = build_task_contract(
            "从 https://example.test 提取5条标题和URL。"
        )

        class ExplodingLLM:
            def invoke(self, _messages):
                raise AssertionError("partial contract progress must not call LLM")

        base_state = {
            "execution_mode": "dp_cli",
            "current_url": "https://example.test",
            "user_task": contract["task"],
            "plan": "extract",
            "execution_log": "",
            "dpcli_task_contract": contract,
            "dpcli_task_progress": {"active_page": 1},
            "dpcli_structured_plan": {
                "step_intent": "extract",
                "action_payload": {"target_ref": "r2"},
                "_contract_action": True,
            },
            "generated_action": {
                "skill": "extract",
                "params": {
                    "target_ref": "r2",
                    "schema": ["title", "url"],
                    "limit": 5,
                },
            },
            "dpcli_result": {
                "ok": True,
                "action": "extract",
                "data": {
                    "page": {"url": "https://example.test"},
                    "items": [
                        {"title": f"Item {index}", "url": f"https://example.test/{index}"}
                        for index in range(3)
                    ],
                },
            },
        }

        config = {"configurable": {"browser": None}}
        first = verifier_node(base_state, config=config, llm=ExplodingLLM())

        self.assertEqual(first.goto, "Observer")
        self.assertEqual(len(first.update["dpcli_task_progress"]["items"]), 3)
        self.assertIn(
            "r2",
            first.update["dpcli_task_progress"]["failed_region_refs"],
        )

        second_state = {
            **base_state,
            "dpcli_task_progress": first.update["dpcli_task_progress"],
            "dpcli_structured_plan": {
                "step_intent": "extract",
                "action_payload": {"target_ref": "r3"},
                "_contract_action": True,
            },
            "generated_action": {
                "skill": "extract",
                "params": {
                    "target_ref": "r3",
                    "schema": ["title", "url"],
                    "limit": 2,
                },
            },
            "dpcli_result": {
                "ok": True,
                "action": "extract",
                "data": {
                    "page": {"url": "https://example.test"},
                    "items": [
                        {"title": f"Item {index}", "url": f"https://example.test/{index}"}
                        for index in range(3, 5)
                    ],
                },
            },
        }

        second = verifier_node(second_state, config=config, llm=ExplodingLLM())

        self.assertEqual(second.goto, "__end__")
        self.assertTrue(second.update["is_complete"])
        self.assertEqual(len(second.update["dpcli_task_progress"]["items"]), 5)

    def test_verifier_ends_graph_when_full_contract_is_satisfied(self):
        contract = build_task_contract(
            "从 https://example.test 提取2条标题和URL。"
        )

        class ExplodingLLM:
            def invoke(self, _messages):
                raise AssertionError("completed task contract must not call LLM")

        command = verifier_node(
            {
                "execution_mode": "dp_cli",
                "current_url": "https://example.test",
                "user_task": contract["task"],
                "plan": "extract",
                "execution_log": "",
                "generated_action": {
                    "skill": "extract",
                    "params": {
                        "target_ref": "r2",
                        "schema": ["title", "url"],
                        "limit": 2,
                    },
                },
                "dpcli_result": {
                    "ok": True,
                    "action": "extract",
                    "data": {
                        "page": {"url": "https://example.test"},
                        "items": [
                            {"title": "One", "url": "https://example.test/1"},
                            {"title": "Two", "url": "https://example.test/2"},
                        ],
                    },
                },
                "dpcli_structured_plan": {
                    "step_intent": "extract",
                    "action_payload": {"target_ref": "r2"},
                    "_contract_action": True,
                },
                "dpcli_task_contract": contract,
                "dpcli_task_progress": {"active_page": 1},
                "finished_steps": [],
                "locator_suggestions": [],
                "reflections": [],
                "_failed_code_cache_ids": [],
                "_failed_dom_cache_ids": [],
            },
            {"configurable": {"browser": None}},
            ExplodingLLM(),
        )

        self.assertEqual(command.goto, "__end__")
        self.assertTrue(command.update["is_complete"])
        self.assertTrue(command.update["verification_result"]["is_done"])

    def test_contract_pagination_click_does_not_fall_through_to_llm(self):
        result = _verify_dpcli_action_with_signals(
            {
                "generated_action": {"skill": "click", "params": {"ref": "e26"}},
                "dpcli_result": {
                    "ok": True,
                    "data": {"page": {"url": "https://example.test/products?page=2"}},
                },
                "dpcli_execution_evidence": {
                    "after_url": "https://example.test/products?page=2",
                    "url_changed": True,
                },
                "dpcli_structured_plan": {
                    "step_intent": "click",
                    "action_payload": {"ref": "e26"},
                    "_contract_action": True,
                },
            },
            "https://example.test/products?page=2",
        )

        self.assertTrue(result["is_success"])
        self.assertFalse(result.get("needs_llm", False))
        self.assertEqual(result["decision_source"], "task_contract")

    def test_wrong_navigation_rows_fail_task_schema_even_if_action_schema_passes(self):
        contract = build_task_contract(
            "从 https://example.test 提取25行球队名称、年份、胜场和负场。"
        )
        result = evaluate_contract_items(
            contract,
            [
                {"title": "Sandbox", "url": "https://example.test/pages/"},
                {"title": "Lessons", "url": "https://example.test/lessons/"},
                {"title": "FAQ", "url": "https://example.test/faq/"},
            ],
            expected_count=25,
        )

        self.assertFalse(result["is_success"])
        self.assertEqual(result["field_coverage"]["year"], 0.0)
        self.assertIn("required field coverage", result["summary"])

    def test_progress_deduplicates_and_completes_across_pages(self):
        contract = build_task_contract(
            "从 https://example.test/products 提取两页，每页2个商品，"
            "字段为名称、价格和URL，合计4条。"
        )
        first = [
            {"title": "A", "price": "1", "url": "https://example.test/a"},
            {"title": "B", "price": "2", "url": "https://example.test/b"},
        ]
        second = [
            {"title": "B", "price": "2", "url": "https://example.test/b"},
            {"title": "C", "price": "3", "url": "https://example.test/c"},
            {"title": "D", "price": "4", "url": "https://example.test/d"},
        ]

        progress = merge_contract_progress({}, first, page_number=1)
        progress = merge_contract_progress(progress, second, page_number=2)

        self.assertEqual(len(progress["items"]), 4)
        self.assertEqual(progress["completed_pages"], [1, 2])
        self.assertTrue(
            evaluate_contract_items(
                contract,
                progress["items"],
                expected_count=contract["min_items"],
            )["is_success"]
        )

    def test_detail_rows_merge_back_into_same_list_identity(self):
        progress = merge_contract_progress(
            {},
            [
                {
                    "title": "Book One",
                    "url": "https://example.test/book/1",
                }
            ],
            page_number=1,
        )
        progress = merge_contract_progress(
            progress,
            [
                {
                    "title": "Book One",
                    "url": "https://example.test/book/1",
                    "description": "A complete description.",
                }
            ],
            page_number=1,
        )

        self.assertEqual(len(progress["items"]), 1)
        self.assertEqual(
            progress["items"][0]["description"],
            "A complete description.",
        )

    def test_verified_page_actions_advance_resume_progress_once(self):
        pagination = _advance_contract_page_progress(
            {
                "dpcli_task_progress": {
                    "active_page": 1,
                    "failed_region_refs": ["r2"],
                },
                "dpcli_structured_plan": {
                    "step_intent": "click",
                    "action_payload": {"ref": "e20", "page_number": 2},
                },
            }
        )
        filtered = _advance_contract_page_progress(
            {
                "dpcli_task_progress": {
                    "active_page": 2,
                    "completed_pages": [1],
                    "failed_region_refs": ["r2"],
                    "filter_applied": False,
                },
                "dpcli_structured_plan": {
                    "step_intent": "type",
                    "action_payload": {"filter_stage": "applied"},
                },
            }
        )
        scrolled = _advance_contract_page_progress(
            {
                "dpcli_task_progress": {
                    "scroll_round": 1,
                    "failed_region_refs": ["r2"],
                },
                "dpcli_structured_plan": {
                    "step_intent": "scroll",
                    "action_payload": {"round": 2},
                },
            }
        )

        self.assertEqual(pagination["active_page"], 2)
        self.assertEqual(pagination["failed_region_refs"], [])
        self.assertTrue(filtered["filter_applied"])
        self.assertEqual(filtered["active_page"], 1)
        self.assertEqual(filtered["completed_pages"], [])
        self.assertEqual(scrolled["scroll_round"], 2)
        self.assertEqual(scrolled["failed_region_refs"], [])

    def test_detail_contract_uses_list_schema_until_batch_finishes(self):
        contract = build_task_contract(
            "打开 https://example.test/books，提取2本书的标题和URL，"
            "然后进入每本书详情页提取简介。"
        )
        list_state = {
            "dpcli_task_contract": contract,
            "generated_action": {
                "skill": "extract",
                "params": {
                    "schema": contract["list_schema"],
                    "limit": 2,
                },
            },
            "dpcli_result": {
                "ok": True,
                "action": "extract",
                "data": {
                    "items": [
                        {"title": "A", "url": "https://example.test/a"},
                        {"title": "B", "url": "https://example.test/b"},
                    ]
                },
            },
            "dpcli_task_progress": {"active_page": 1},
        }

        verification = _contract_action_verification(list_state, "extract")
        progress, _evaluation, is_done = _merge_dpcli_contract_progress(list_state)

        self.assertTrue(verification["is_success"])
        self.assertTrue(progress["list_complete"])
        self.assertFalse(is_done)

    def test_detail_batch_waits_for_all_list_pages_and_uses_cumulative_items(self):
        contract = build_task_contract(
            "打开 https://example.test/books，抓取前2页，每页2本书的标题和URL，"
            "然后进入每本书详情页提取简介。"
        )
        first_page_state = {
            "user_task": contract["task"],
            "current_url": contract["target_url"],
            "dpcli_task_contract": contract,
            "dpcli_task_progress": {
                "items": [
                    {"title": "A", "url": "https://example.test/a"},
                    {"title": "B", "url": "https://example.test/b"},
                ],
                "completed_pages": [1],
                "active_page": 1,
                "list_complete": False,
            },
            "dpcli_result": {
                "ok": True,
                "action": "extract",
                "data": {
                    "items": [
                        {"title": "A", "url": "https://example.test/a"},
                        {"title": "B", "url": "https://example.test/b"},
                    ]
                },
            },
            "dpcli_detail_batch_ran": False,
        }
        self.assertFalse(should_run_detail_batch(first_page_state))

        complete_state = dict(first_page_state)
        complete_state["dpcli_task_progress"] = {
            "items": [
                {"title": "A", "url": "https://example.test/a"},
                {"title": "B", "url": "https://example.test/b"},
                {"title": "C", "url": "https://example.test/c"},
                {"title": "D", "url": "https://example.test/d"},
            ],
            "completed_pages": [1, 2],
            "active_page": 2,
            "list_complete": True,
        }
        complete_state["dpcli_result"] = {
            "ok": True,
            "action": "extract",
            "data": {
                "items": [
                    {"title": "C", "url": "https://example.test/c"},
                    {"title": "D", "url": "https://example.test/d"},
                ]
            },
        }

        self.assertTrue(should_run_detail_batch(complete_state))
        action = build_detail_batch_action(complete_state)
        self.assertEqual(len(action["params"]["items"]), 4)
        self.assertEqual(action["params"]["schema"], ["description"])


if __name__ == "__main__":
    unittest.main()
