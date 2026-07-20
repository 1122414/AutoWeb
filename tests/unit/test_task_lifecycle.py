from __future__ import annotations

import json

from skills.task_lifecycle import TaskLifecycle


def _capability_map():
    return {
        "forms": [
            {
                "inputs": [
                    {
                        "ref": "e1",
                        "name": "类别框",
                        "role": "textbox",
                        "input_type": "text",
                    },
                    {
                        "ref": "e2",
                        "name": "搜索框",
                        "role": "searchbox",
                        "input_type": "search",
                    },
                ]
            }
        ]
    }


def test_compile_builds_versioned_composite_phases_and_stop_condition():
    lifecycle = TaskLifecycle()
    contract = lifecycle.compile(
        "打开 https://example.test/products，在类别框输入“Fiction”，"
        "再在搜索框输入“light”，筛选后抓取前2页标题、URL，"
        "然后进入详情页提取描述，直到看到“Stop Book”。"
    )

    assert contract["version"] == 3
    assert [item["value"] for item in contract["filters"]] == [
        "Fiction",
        "light",
    ]
    assert contract["phases"] == [
        "navigate",
        "filter:0",
        "filter:1",
        "collect:pagination",
        "details",
        "complete",
    ]
    assert contract["stop_conditions"]["until_text"] == "Stop Book"
    json.dumps(contract, ensure_ascii=False)


def test_decide_sequences_multiple_filters_before_collection():
    lifecycle = TaskLifecycle()
    contract = lifecycle.compile(
        "在类别框输入“Fiction”，再在搜索框输入“light”，筛选后抓取2条标题。"
    )
    state = {
        "user_task": contract["task"],
        "current_url": "",
        "dpcli_task_contract": contract,
        "dpcli_task_progress": {},
        "dpcli_agent_view": {"capability_map": _capability_map()},
    }
    contract["target_url"] = ""

    first, updates = lifecycle.decide(state, contract)
    assert first["step_intent"] == "type"
    assert first["action_payload"]["ref"] == "e1"
    assert first["action_payload"]["filter_index"] == 0

    progress = lifecycle.advance_verified_page(
        {
            **state,
            "dpcli_task_progress": updates["dpcli_task_progress"],
            "dpcli_structured_plan": first,
        }
    )
    second, _ = lifecycle.decide(
        {**state, "dpcli_task_progress": progress},
        contract,
    )
    assert second["action_payload"]["ref"] == "e2"
    assert second["action_payload"]["filter_index"] == 1


def test_conditional_stop_can_finish_before_numeric_target():
    lifecycle = TaskLifecycle()
    contract = lifecycle.compile(
        "抓取20条标题和URL，直到看到“Stop Book”。"
    )
    state = {
        "dpcli_task_contract": contract,
        "dpcli_task_progress": {"active_page": 1, "items": []},
        "generated_action": {"skill": "extract", "params": {"limit": 10}},
        "dpcli_result": {
            "ok": True,
            "data": {
                "items": [
                    {"title": "First", "url": "https://example.test/1"},
                    {"title": "Stop Book", "url": "https://example.test/2"},
                ]
            },
        },
    }

    progress, evaluation, done = lifecycle.merge_verified_result(state)
    assert done is True
    assert evaluation["is_success"] is True
    assert progress["stop_condition_met"] is True
    assert progress["stop_reason"] == "until_text:Stop Book"


def test_lifecycle_checkpoint_roundtrip_is_json_safe():
    lifecycle = TaskLifecycle()
    state = {
        "user_task": "抓取2条标题",
        "dpcli_task_contract": lifecycle.compile("抓取2条标题"),
        "dpcli_task_progress": {
            "items": [{"title": "A"}],
            "completed_pages": [1],
        },
    }

    payload = json.loads(
        json.dumps(lifecycle.checkpoint(state), ensure_ascii=False)
    )
    restored = lifecycle.restore(payload)

    assert restored["dpcli_task_contract"]["version"] == 3
    assert restored["dpcli_task_progress"]["items"] == [{"title": "A"}]
