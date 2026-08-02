from scripts.benchmark.benchmark_cross_site_tasks import (
    CASES,
    _journey_evaluation,
)


def test_suite_contains_twenty_real_cross_page_cases():
    assert len(CASES) == 20
    assert all(case.url.startswith(("http://", "https://")) for case in CASES.values())
    assert all("web-scraping" not in case.url for case in CASES.values())


def test_journey_evaluator_requires_entry_target_actions_and_safe_terminal():
    case = CASES["iiice_jd_charger"]
    run = {
        "status": "completed",
        "events": [
            {"current_url": "https://www.iiice.cn/"},
            {"generated_action": {"skill": "open", "params": {"url": case.url}}},
            {"dpcli_result": {"ok": True, "action": "open", "page_url": case.url}},
            {"generated_action": {"skill": "type", "params": {"text": "site:jd.com 65W氮化镓充电器"}}},
            {"dpcli_result": {"ok": True, "action": "type", "page_url": "https://www.bing.com/search?q=jd"}},
            {"generated_action": {"skill": "click", "params": {"ref": "e1"}}},
            {"dpcli_result": {"ok": True, "action": "click", "page_url": "https://search.jd.com/Search?keyword=65w"}},
            {"generated_action": {"skill": "click", "params": {"ref": "e9"}}},
            {"dpcli_result": {"ok": True, "action": "click", "page_url": "https://cart.jd.com/cart.action"}},
        ],
        "results": [],
    }

    result = _journey_evaluation(case, run)

    assert result["passed"] is True
    assert result["checks"]["target_site_reached"] is True
    assert result["checks"]["safe_terminal_reached"] is True


def test_journey_evaluator_rejects_direct_target_without_required_entry():
    case = CASES["iiice_jd_charger"]
    run = {
        "status": "completed",
        "events": [
            {"current_url": "https://search.jd.com/Search?keyword=65w"},
            {"generated_action": {"skill": "type", "params": {"text": "65w"}}},
            {"dpcli_result": {"ok": True, "action": "type", "page_url": "https://cart.jd.com/cart.action"}},
        ],
        "results": [],
    }

    result = _journey_evaluation(case, run)

    assert result["passed"] is False
    assert result["checks"]["started_at_required_entry"] is False


def test_journey_evaluator_rejects_irreversible_click():
    case = CASES["iiice_jd_charger"]
    run = {
        "status": "completed",
        "events": [
            {"current_url": case.url},
            {"dpcli_structured_plan": {"target_request": {"target_hint": "提交订单", "text_or_name": ["提交订单"]}}},
            {"generated_action": {"skill": "click", "params": {"ref": "e88"}}},
            {"dpcli_result": {"ok": True, "action": "click", "page_url": "https://trade.jd.com/shopping/order/getOrderInfo.action"}},
        ],
        "results": [],
    }

    result = _journey_evaluation(case, run)

    assert result["checks"]["no_irreversible_action_clicked"] is False


def test_journey_evaluator_accepts_nested_policy_blocker_after_target_entry():
    case = CASES["iiice_jd_charger"]
    run = {
        "status": "failed",
        "events": [
            {"current_url": case.url},
            {
                "dpcli_result": {
                    "ok": True,
                    "action": "click",
                    "page_url": "https://passport.jd.com/new/login.aspx",
                }
            },
            {
                "dpcli_result": {
                    "ok": False,
                    "action": "snapshot",
                    "page_url": "https://passport.jd.com/new/login.aspx",
                    "error": {
                        "code": "site_blocked",
                        "details": {
                            "blocking_signal": {
                                "detected": True,
                                "kind": "login_required",
                            }
                        },
                    },
                }
            },
        ],
        "results": [],
    }

    result = _journey_evaluation(case, run)

    assert result["blocker"] == "login_required"
    assert result["passed"] is True
    assert result["checks"]["minimum_successful_actions"] is True
    assert result["checks"]["required_text_input"] is True


def test_journey_evaluator_accepts_declared_app_handoff_at_required_entry():
    case = CASES["iiice_food_delivery"]
    run = {
        "status": "completed",
        "events": [
            {"current_url": case.url},
            {
                "generated_action": {
                    "skill": "click",
                    "params": {"locator": "xpath://div[.//p]"},
                }
            },
            {
                "dpcli_result": {
                    "ok": True,
                    "action": "click",
                    "page_url": case.url,
                }
            },
            {
                "dpcli_structured_plan": {
                    "step_intent": "finish",
                    "safe_stop": True,
                    "blocker_kind": "app_required",
                }
            },
        ],
        "results": [],
    }

    result = _journey_evaluation(case, run)

    assert result["passed"] is True
    assert result["blocker"] == "app_required"
    assert result["checks"]["target_site_reached"] is True


def test_journey_evaluator_accepts_specific_robots_denial_not_generic_policy():
    case = CASES["iiice_jd_charger"]
    run = {
        "status": "failed",
        "events": [
            {"current_url": case.url},
            {
                "dpcli_result": {
                    "ok": False,
                    "action": "type",
                    "page_url": case.url,
                    "error": {
                        "code": "site_policy_denied",
                        "details": {
                            "policy_decision": {"reason": "robots_denied"}
                        },
                    },
                }
            },
        ],
        "results": [],
    }

    result = _journey_evaluation(case, run)
    assert result["blocker"] == "robots_denied"
    assert result["passed"] is True
