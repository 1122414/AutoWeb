from __future__ import annotations

import time
from typing import Literal

from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from core.state_v2 import AgentState
from core.nodes._verification import (
    _build_verification_result,
    _parse_verifier_result_content,
    _normalize_failure_scope,
)
from core.nodes._cache import _handle_cache_failure
from core.nodes._dpcli import _dpcli_result_url
from prompts.verifier_prompts import VERIFIER_CHECK_PROMPT
from skills.logger import logger

def verifier_node(state: AgentState, config: RunnableConfig, llm) -> Command[Literal["Observer", "Planner", "Executor", "RAGNode"]]:
    """[Verifier] 验收并决定下一步"""
    logger.info("\n🔍 [Verifier] 正在验收...")

    log = state.get("execution_log", "")
    task = state.get("user_task", "")
    current_plan = state.get("plan", "Unknown Plan")
    code_source = state.get("_code_source", "llm")
    current_suggestions = state.get("locator_suggestions", [])

    # 获取最新标签页（处理新标签页打开的情况）
    dpcli_current_url = ""
    if state.get("execution_mode") == "dp_cli":
        dpcli_current_url = _dpcli_result_url(state.get("dpcli_result") or {})

    browser = config["configurable"].get("browser")
    if dpcli_current_url:
        tab = None
        current_url = dpcli_current_url
    elif browser:
        time.sleep(0.3)  # 短暂等待，让新标签页有时间创建
        tab = browser.latest_tab
        # 等待页面加载
        try:
            tab.wait.load_start()
            tab.wait(0.3)
        except TimeoutError as e:
            logger.warning(f"Page load timeout in Verifier: {e}")
        except Exception as e:
            logger.debug(f"Wait interrupted in Verifier: {e}")
        current_url = tab.url if tab else ""
    else:
        tab = None
        current_url = ""

    logger.info(f"   -> 当前验收 URL: {current_url[:60]}...")
    logger.info(f"   📦 代码来源: {code_source}")

    # 1. 快速失败检查（仅致命错误）
    fatal_keywords = ["Runtime Error:", "Traceback", "ElementNotFound",
                      "TimeoutException", "Execution Failed", "Critical"]
    for kw in fatal_keywords:
        if kw in log:
            logger.info(f"⚡ [Verifier] Deterministic Fail: {kw}")

            # 缓存代码失败：跳 Planner，标记失败
            if code_source == "cache":
                return _handle_cache_failure(state, {
                    "messages": [AIMessage(content=f"【缓存验收失败】{kw}")],
                    "reflections": [f"缓存代码验收失败: {kw}"],
                    "verification_result": _build_verification_result(
                        is_success=False,
                        is_done=False,
                        summary=f"缓存代码验收失败: {kw}",
                        source="verifier",
                        failure_scope="local",
                        failed_action=current_plan,
                        evidence=kw,
                        fix_hint="更换执行方式或修复当前失败定位，不要复用该缓存代码",
                    ),
                    "is_complete": False
                })

            # LLM 代码失败：回 Observer
            return Command(
                update={
                    "messages": [AIMessage(content=f"Status: STEP_FAIL ({kw})")],
                    "reflections": [f"Step Failed: {current_plan}. Error: {kw}"],
                    "verification_result": _build_verification_result(
                        is_success=False,
                        is_done=False,
                        summary=f"步骤失败: {kw}",
                        source="verifier",
                        failure_scope="local",
                        failed_action=current_plan,
                        evidence=kw,
                        fix_hint="仅修复当前失败步骤，不要全局重写",
                    ),
                    "is_complete": False
                },
                goto="Observer"
            )

    # 2. LLM 验收（优化 Prompt）
    prompt = VERIFIER_CHECK_PROMPT.format(
        user_task=task,
        current_plan=current_plan,
        current_url=current_url,
        log=log[-2000:],
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content

    parsed = _parse_verifier_result_content(content)
    is_success = parsed["is_success"]
    summary = parsed["summary"]

    # 3. 返回验收结果
    logger.info(f"\n📋 [Verifier] LLM 判定:")
    logger.info(f"   Status: {'SUCCESS' if is_success else 'FAIL'}")
    logger.info(f"   Summary: {summary[:100]}")

    # 将验收结果存入 State，供 main.py 读取和覆盖
    updates = {
        "messages": [response],
        "is_complete": False,  # Verifier 不再判断任务完成，交给 Planner
        "current_url": current_url,
        "verification_result": _build_verification_result(
            is_success=is_success,
            is_done=False,  # 由 Planner 判断
            summary=summary,
            source="verifier",
            failure_scope=parsed.get("failure_scope", "local"),
            failed_action=parsed.get("failed_action", "") or current_plan,
            failed_locator=parsed.get("failed_locator", ""),
            evidence=parsed.get("evidence", ""),
            fix_hint=parsed.get("fix_hint", ""),
        ),
    }

    if is_success:
        updates["finished_steps"] = [summary]
        # 一旦本步成功，释放失败窗口内的缓存黑名单
        updates["_failed_code_cache_ids"] = []
        updates["_failed_dom_cache_ids"] = []
        updates["_cache_hit_id"] = None

        if state.get("execution_mode") == "dp_cli":
            try:
                from config import ACTION_CACHE_ENABLED
                if ACTION_CACHE_ENABLED and state.get("_action_source") != "action_cache":
                    from skills.action_cache import action_cache_manager
                    cache_id = action_cache_manager.save(
                        user_task=task,
                        goal=current_plan,
                        url=current_url,
                        action=state.get("generated_action") or {},
                        snapshot_view=state.get("dpcli_snapshot_view"),
                        result_summary=summary,
                    )
                    logger.info(f"   💾 [ActionCache] 已写入: {cache_id}")
            except Exception as action_store_exc:
                logger.info(f"   ⚠️ [ActionCache] 写入异常: {action_store_exc}")

            try:
                from skills.dpcli_crawl_policy import (
                    build_detail_batch_action,
                    should_run_detail_batch,
                )
                policy_state = dict(state)
                policy_state.update(updates)
                if should_run_detail_batch(policy_state):
                    detail_action = build_detail_batch_action(policy_state)
                    item_count = len(detail_action.get("params", {}).get("items", []))
                    logger.info(
                        f"   📦 dp_cli extract OK + 详情任务({item_count}) → batch-detail-extract")
                    updates.update({
                        "generated_action": detail_action,
                        "generated_code": None,
                        "execution_mode": "dp_cli",
                        "dpcli_detail_batch_ran": True,
                        "_action_source": "policy",
                    })
                    return Command(update=updates, goto="Executor")
            except Exception as policy_exc:
                logger.info(f"   ⚠️ dp_cli 详情批处理策略跳过: {policy_exc}")

        # 检查是否需要存代码或策略到缓存 → RAGNode
        code = state.get("generated_code", "")
        code_source_val = state.get("_code_source", "")
        observer_source = state.get("_observer_source", "")

        needs_store_code = bool(code and len(
            code) > 50 and code_source_val != "cache")
        needs_store_dom = bool(observer_source == "observer")

        if needs_store_code or needs_store_dom:
            logger.info(
                f"   📚 Step OK + 需缓存代码({needs_store_code})/策略({needs_store_dom}) → RAGNode")
            updates["rag_task_type"] = "store_cache"
            return Command(update=updates, goto="RAGNode")

        logger.info("   🔄 Step OK, 继续下一步...")
        return Command(update=updates, goto="Observer")
    else:
        logger.info("   ❌ Step Failed")
        updates["reflections"] = [f"Step Failed: {summary}"]
        failure_scope = _normalize_failure_scope(
            updates["verification_result"].get("failure_scope", "local"))
        # 仅在 global 失败时回滚整条最新策略；local 失败保留上下文做定向修复
        if failure_scope == "global":
            updates["locator_suggestions"] = {
                "__replace__": current_suggestions[:-1]} if current_suggestions else None

        # 缓存代码验收失败：失效缓存 + 跳 Planner
        if code_source == "cache":
            return _handle_cache_failure(state, updates)

        # LLM 代码失败：回 Observer 重试
        return Command(update=updates, goto="Observer")