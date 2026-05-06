from __future__ import annotations

import re
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
from core.nodes._dpcli import _dpcli_result_url, _dpcli_action_kind, _compact_result_evidence
from prompts.verifier_prompts import VERIFIER_CHECK_PROMPT
from skills.logger import logger


def _verify_dpcli_action_deterministically(state):
    """Deterministic verification for dp_cli observation/data actions.

    Returns a verification_result dict, or None to fall through to LLM verifier.
    """
    action = state.get("generated_action") or {}
    result = state.get("dpcli_result") or {}
    kind = _dpcli_action_kind(action)
    skill = str(action.get("skill") or "").lower()

    if not result.get("ok"):
        return None

    if kind == "observation":
        return _build_verification_result(
            is_success=True,
            is_done=False,
            summary=f"observation succeeded: {skill}",
            source="verifier",
            failure_scope="local",
            evidence=_compact_result_evidence(result),
            fix_hint="continue planning with updated snapshot context",
        )

    if kind == "data":
        data = result.get("data") or {}
        items = data.get("items") if isinstance(data, dict) else None
        if items and isinstance(items, list) and len(items) > 0:
            return _build_verification_result(
                is_success=True,
                is_done=False,
                summary=f"data action succeeded: {skill} ({len(items)} items)",
                source="verifier",
                failure_scope="local",
                evidence=_compact_result_evidence(result),
            )
        return _build_verification_result(
            is_success=False,
            is_done=False,
            summary=f"data action returned no usable items: {skill}",
            source="verifier",
            failure_scope="local",
            evidence=_compact_result_evidence(result),
            fix_hint="select a better data region or list ref",
        )

    return None


def _handle_dpcli_success_after_verification(
    state,
    updates,
    task,
    current_plan,
    current_url,
    summary,
):
    """Shared post-verification logic for dp_cli success paths.

    Runs ActionCache save and detail batch policy.
    Returns Command(goto="Executor") if batch is triggered, else None.
    """
    # ActionCache save (failure must not block detail policy)
    try:
        from config import ACTION_CACHE_ENABLED
        if ACTION_CACHE_ENABLED and state.get("_action_source") != "action_cache":
            from skills.action_cache import action_cache_manager
            action_cache_manager.save(
                user_task=task,
                goal=current_plan,
                url=current_url,
                action=state.get("generated_action") or {},
                snapshot_view=state.get("dpcli_snapshot_view"),
                result_summary=summary,
            )
    except Exception as action_store_exc:
        logger.info(f"   [ActionCache] save exception: {action_store_exc}")

    # Detail batch policy
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
                f"   [Verifier] extract OK + detail task({item_count}) -> batch-detail-extract")
            updates.update({
                "generated_action": detail_action,
                "generated_code": None,
                "execution_mode": "dp_cli",
                "dpcli_detail_batch_ran": True,
                "_action_source": "policy",
            })
            return Command(update=updates, goto="Executor")
    except Exception as policy_exc:
        logger.info(f"   [Verifier] detail batch policy skip: {policy_exc}")

    return None


def _build_dpcli_verifier_prompt(state, task, current_plan, current_url, log):
    """Build verifier prompt with dp_cli action context when appropriate."""
    if state.get("execution_mode") != "dp_cli":
        return VERIFIER_CHECK_PROMPT.format(
            user_task=task,
            current_plan=current_plan,
            current_url=current_url,
            log=log[-2000:],
            generated_action="",
            dpcli_action_kind="",
            dpcli_result_summary="",
            structured_plan="",
        )

    import json
    action = state.get("generated_action") or {}
    kind = _dpcli_action_kind(action)
    result = state.get("dpcli_result") or {}
    structured_plan = state.get("dpcli_structured_plan") or {}

    return VERIFIER_CHECK_PROMPT.format(
        user_task=task,
        current_plan=current_plan,
        current_url=current_url,
        log=log[-2000:],
        generated_action=json.dumps(action, ensure_ascii=False, indent=2),
        dpcli_action_kind=kind,
        dpcli_result_summary=json.dumps(
            _compact_result_evidence(result), ensure_ascii=False, indent=2),
        structured_plan=json.dumps(
            structured_plan, ensure_ascii=False, indent=2),
    )


def _route_by_error_type(state, current_plan, code_source):
    """P0-4: Structured error_type fast path before generic keyword scanning.

    Returns Command or None (to fall through to keyword regex scan).
    """
    error_type = state.get("error_type")
    if not error_type:
        return None

    error_type = str(error_type).strip().lower()

    # --- Coder fix category: syntax/code generation errors → Coder ---
    if error_type in ("syntax", "dpcli_action_json", "dpcli_invalid_action", "syntax_max_retry"):
        summary = f"structured error_type fast-path: {error_type}"
        if code_source == "cache":
            return _handle_cache_failure(state, {
                "messages": [AIMessage(content=f"【结构化错误验收失败】{summary}")],
                "reflections": [summary],
                "verification_result": _build_verification_result(
                    is_success=False,
                    is_done=False,
                    summary=summary,
                    source="verifier",
                    failure_scope="local",
                    failed_action=current_plan,
                    evidence=f"error_type={error_type}",
                    fix_hint="code generation error — regenerate action/code",
                    decision_source="error_type",
                ),
                "is_complete": False,
            })
        return Command(
            update={
                "messages": [AIMessage(content=f"Status: STEP_FAIL ({error_type})")],
                "reflections": [summary],
                "verification_result": _build_verification_result(
                    is_success=False,
                    is_done=False,
                    summary=summary,
                    source="verifier",
                    failure_scope="local",
                    failed_action=current_plan,
                    evidence=f"error_type={error_type}",
                    fix_hint="code generation error — regenerate action/code",
                    decision_source="error_type",
                ),
                "is_complete": False,
            },
            goto="Coder",
        )

    # --- Locator category: stale refs, missing snapshots → Observer (local fix) ---
    if error_type in ("locator", "dpcli_ref_stale", "dpcli_snapshot_missing"):
        summary = f"structured error_type fast-path: {error_type}"
        if code_source == "cache":
            return _handle_cache_failure(state, {
                "messages": [AIMessage(content=f"【结构化错误验收失败】{summary}")],
                "reflections": [summary],
                "verification_result": _build_verification_result(
                    is_success=False,
                    is_done=False,
                    summary=summary,
                    source="verifier",
                    failure_scope="local",
                    failed_action=current_plan,
                    evidence=f"error_type={error_type}",
                    fix_hint="locator/snapshot issue — re-observe page and retry",
                    decision_source="error_type",
                ),
                "is_complete": False,
            })
        return Command(
            update={
                "messages": [AIMessage(content=f"Status: STEP_FAIL ({error_type})")],
                "reflections": [summary],
                "verification_result": _build_verification_result(
                    is_success=False,
                    is_done=False,
                    summary=summary,
                    source="verifier",
                    failure_scope="local",
                    failed_action=current_plan,
                    evidence=f"error_type={error_type}",
                    fix_hint="locator/snapshot issue — re-observe page and retry",
                    decision_source="error_type",
                ),
                "is_complete": False,
            },
            goto="Observer",
        )

    # --- Security/retry category → Planner (re-plan) ---
    if error_type in ("security", "security_max_retry"):
        summary = f"structured error_type fast-path: {error_type}"
        if code_source == "cache":
            return _handle_cache_failure(state, {
                "messages": [AIMessage(content=f"【结构化错误验收失败】{summary}")],
                "reflections": [summary],
                "verification_result": _build_verification_result(
                    is_success=False,
                    is_done=False,
                    summary=summary,
                    source="verifier",
                    failure_scope="local",
                    failed_action=current_plan,
                    evidence=f"error_type={error_type}",
                    fix_hint="security issue or max retries exceeded — re-plan approach",
                    decision_source="error_type",
                ),
                "is_complete": False,
            })
        return Command(
            update={
                "messages": [AIMessage(content=f"Status: STEP_FAIL ({error_type})")],
                "reflections": [summary],
                "verification_result": _build_verification_result(
                    is_success=False,
                    is_done=False,
                    summary=summary,
                    source="verifier",
                    failure_scope="local",
                    failed_action=current_plan,
                    evidence=f"error_type={error_type}",
                    fix_hint="security issue or max retries exceeded — re-plan approach",
                    decision_source="error_type",
                ),
                "is_complete": False,
            },
            goto="Planner",
        )

    # --- Critical → global failure, re-plan ---
    if error_type == "critical":
        summary = f"structured error_type fast-path: {error_type}"
        if code_source == "cache":
            return _handle_cache_failure(state, {
                "messages": [AIMessage(content=f"【结构化错误验收失败】{summary}")],
                "reflections": [summary],
                "verification_result": _build_verification_result(
                    is_success=False,
                    is_done=False,
                    summary=summary,
                    source="verifier",
                    failure_scope="global",
                    failed_action=current_plan,
                    evidence=f"error_type={error_type}",
                    fix_hint="critical error — full re-plan needed",
                    decision_source="error_type",
                ),
                "is_complete": False,
            })
        return Command(
            update={
                "messages": [AIMessage(content=f"Status: STEP_FAIL ({error_type})")],
                "reflections": [summary],
                "verification_result": _build_verification_result(
                    is_success=False,
                    is_done=False,
                    summary=summary,
                    source="verifier",
                    failure_scope="global",
                    failed_action=current_plan,
                    evidence=f"error_type={error_type}",
                    fix_hint="critical error — full re-plan needed",
                    decision_source="error_type",
                ),
                "is_complete": False,
            },
            goto="Planner",
        )

    return None  # unknown error_type — fall through to keyword scan


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

    # 1. P0-4: Structured error_type fast path (before generic keyword scan)
    error_type_route = _route_by_error_type(state, current_plan, code_source)
    if error_type_route is not None:
        return error_type_route

    # 2. Regex-based fatal keyword pattern matching (fallback when no structured error_type)
    fatal_patterns = [
        (re.search(r'^\s*(?:Runtime Error|Traceback)', log, re.MULTILINE), "Runtime Error/Traceback"),
        (re.search(r'\bElementNotFound\b', log), "ElementNotFound"),
        (re.search(r'\bTimeoutException\b', log), "TimeoutException"),
        (re.search(r'^\s*Execution Failed', log, re.MULTILINE), "Execution Failed"),
        (re.search(r'\bCritical\b.*\bError\b', log), "Critical Error"),
    ]
    for match, label in fatal_patterns:
        if match:
            logger.info(f"⚡ [Verifier] Deterministic Fail (keyword_fallback): {label}")

            # 缓存代码失败：跳 Planner，标记失败
            if code_source == "cache":
                return _handle_cache_failure(state, {
                    "messages": [AIMessage(content=f"【缓存验收失败】{label}")],
                    "reflections": [f"缓存代码验收失败: {label}"],
                    "verification_result": _build_verification_result(
                        is_success=False,
                        is_done=False,
                        summary=f"缓存代码验收失败: {label}",
                        source="verifier",
                        failure_scope="local",
                        failed_action=current_plan,
                        evidence=label,
                        fix_hint="更换执行方式或修复当前失败定位，不要复用该缓存代码",
                        decision_source="keyword_fallback",
                    ),
                    "is_complete": False,
                })

            # LLM 代码失败：回 Observer
            return Command(
                update={
                    "messages": [AIMessage(content=f"Status: STEP_FAIL ({label})")],
                    "reflections": [f"Step Failed: {current_plan}. Error: {label}"],
                    "verification_result": _build_verification_result(
                        is_success=False,
                        is_done=False,
                        summary=f"步骤失败: {label}",
                        source="verifier",
                        failure_scope="local",
                        failed_action=current_plan,
                        evidence=label,
                        fix_hint="仅修复当前失败步骤，不要全局重写",
                        decision_source="keyword_fallback",
                    ),
                    "is_complete": False,
                },
                goto="Observer",
            )

    # 3. dp_cli deterministic verification (before LLM)
    if state.get("execution_mode") == "dp_cli":
        deterministic = _verify_dpcli_action_deterministically(state)
        if deterministic is not None:
            is_success = deterministic["is_success"]
            summary = deterministic["summary"]
            logger.info(f"\n   [Verifier] dp_cli deterministic: {'SUCCESS' if is_success else 'FAIL'} ({summary})")

            updates = {
                "messages": [AIMessage(content=f"【dp_cli验收】{summary}")],
                "is_complete": False,
                "current_url": current_url,
                "verification_result": deterministic,
            }

            if is_success:
                action_kind = _dpcli_action_kind(state.get("generated_action") or {})
                if action_kind != "observation":
                    updates["finished_steps"] = [summary]
                updates["_failed_code_cache_ids"] = []
                updates["_failed_dom_cache_ids"] = []
                updates["_cache_hit_id"] = None

                if action_kind == "data":
                    detail_cmd = _handle_dpcli_success_after_verification(
                        state=state,
                        updates=updates,
                        task=task,
                        current_plan=current_plan,
                        current_url=current_url,
                        summary=summary,
                    )
                    if detail_cmd is not None:
                        return detail_cmd

                logger.info("   [Verifier] dp_cli action succeeded, continuing to Observer")
                return Command(update=updates, goto="Observer")

            updates["reflections"] = [f"dp_cli step failed: {summary}"]
            logger.info("   [Verifier] dp_cli action failed, returning to Observer")
            return Command(update=updates, goto="Observer")

    # 4. LLM 验收（优化 Prompt）
    prompt = _build_dpcli_verifier_prompt(state, task, current_plan, current_url, log)
    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content

    parsed = _parse_verifier_result_content(content)
    is_success = parsed["is_success"]
    summary = parsed["summary"]

    # 返回验收结果
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
            detail_cmd = _handle_dpcli_success_after_verification(
                state=state,
                updates=updates,
                task=task,
                current_plan=current_plan,
                current_url=current_url,
                summary=summary,
            )
            if detail_cmd is not None:
                return detail_cmd

        # 检查是否需要存代码或策略到缓存 → RAGNode

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