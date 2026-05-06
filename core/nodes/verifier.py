from __future__ import annotations

import re
import time
import urllib.parse
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


def _check_target_confidence(state):
    """Check TargetSelector confidence for click/type/select actions.

    Returns:
        (confidence: float, ref_match: bool, status: str)
    """
    target_result = state.get("dpcli_target_result") or {}
    status = str(target_result.get("status") or "")
    confidence = float(target_result.get("confidence", 0))

    action = state.get("generated_action") or {}
    params = action.get("params") or {}
    target_ref = str(target_result.get("target_ref") or "")
    action_ref = str(params.get("ref") or params.get("target_ref") or "")
    ref_match = bool(target_ref and action_ref and target_ref == action_ref)

    return (confidence, ref_match, status)


def _verify_dpcli_action_with_signals(state, current_url):
    """Enhanced dp_cli action verification with URL matching, schema validation,
    and target confidence signals.

    Returns a verification_result dict, or None to fall through to LLM verifier.
    """
    from config import (
        VERIFIER_MIN_TARGET_CONFIDENCE,
        VERIFIER_SCHEMA_COVERAGE_THRESHOLD,
        VERIFIER_ALLOW_LOW_CONFIDENCE_SUCCESS,
        VERIFIER_LLM_REQUIRED_FOR_AMBIGUOUS_PAGE,
    )

    action = state.get("generated_action") or {}
    result = state.get("dpcli_result") or {}
    kind = _dpcli_action_kind(action)
    skill = str(action.get("skill") or "").lower()

    if not result.get("ok"):
        return None

    # --- Observation actions (unchanged behavior + decision_source) ---
    if kind == "observation":
        return _build_verification_result(
            is_success=True,
            is_done=False,
            summary=f"observation succeeded: {skill}",
            source="verifier",
            failure_scope="local",
            evidence=_compact_result_evidence(result),
            fix_hint="continue planning with updated snapshot context",
            decision_source="dpcli_observation",
        )

    # --- Data actions with schema validation ---
    if kind == "data":
        data = result.get("data") or {}
        items = data.get("items") if isinstance(data, dict) else None
        has_items = items and isinstance(items, list) and len(items) > 0

        # No items → deterministic fail
        if not has_items:
            return _build_verification_result(
                is_success=False,
                is_done=False,
                summary=f"data action returned no usable items: {skill}",
                source="verifier",
                failure_scope="local",
                evidence=_compact_result_evidence(result),
                fix_hint="select a better data region or list ref",
                decision_source="dpcli_data",
            )

        # Get schema from action params or structured_plan
        structured_plan = state.get("dpcli_structured_plan") or {}
        schema = (
            action.get("params", {}).get("schema")
            or structured_plan.get("action_payload", {}).get("schema")
        )

        # No schema defined → old behavior (items exist = success)
        if not schema or not isinstance(schema, list) or len(schema) == 0:
            return _build_verification_result(
                is_success=True,
                is_done=False,
                summary=f"data action succeeded: {skill} ({len(items)} items)",
                source="verifier",
                failure_scope="local",
                evidence=_compact_result_evidence(result),
                decision_source="dpcli_data",
            )

        # Schema is defined → validate field coverage across items
        schema_fields = [str(f).strip().lower() for f in schema if f]
        if not schema_fields:
            return _build_verification_result(
                is_success=True,
                is_done=False,
                summary=f"data action succeeded: {skill} ({len(items)} items)",
                source="verifier",
                failure_scope="local",
                evidence=_compact_result_evidence(result),
                decision_source="dpcli_data",
            )

        fields_present = 0
        for field in schema_fields:
            for item in items:
                if isinstance(item, dict) and field in {str(k).strip().lower() for k in item}:
                    fields_present += 1
                    break

        coverage = fields_present / len(schema_fields)

        if coverage >= VERIFIER_SCHEMA_COVERAGE_THRESHOLD:
            return _build_verification_result(
                is_success=True,
                is_done=False,
                summary=f"data action succeeded: {skill} ({len(items)} items, schema coverage {coverage:.0%})",
                source="verifier",
                failure_scope="local",
                evidence=_compact_result_evidence(result),
                decision_source="schema_match",
            )

        # Below threshold but has items → defer to LLM for judgment
        return None

    # --- Page actions with URL/signal rules ---
    if kind == "page":
        structured_plan = state.get("dpcli_structured_plan") or {}
        execution_evidence = state.get("dpcli_execution_evidence") or {}
        after_url = execution_evidence.get("after_url") or current_url or ""

        # Get expected URL from structured_plan or action params
        expected_url = (
            structured_plan.get("action_payload", {}).get("url")
            or action.get("params", {}).get("url")
            or ""
        )

        step_intent = str(structured_plan.get("step_intent") or "").lower()

        def _url_matches(expected, after):
            """3-tier URL matching for navigation verification.

            Tier 1: exact match
            Tier 2: same netloc + path prefix match
            Tier 3: expected netloc contained in after netloc
            """
            if not expected or not after:
                return False
            if expected == after:
                return True
            expected_parsed = urllib.parse.urlparse(expected)
            after_parsed = urllib.parse.urlparse(after)
            if (expected_parsed.netloc
                    and after_parsed.netloc
                    and expected_parsed.netloc == after_parsed.netloc
                    and expected_parsed.path
                    and after_parsed.path.startswith(expected_parsed.path)):
                return True
            if expected_parsed.netloc and expected_parsed.netloc in after_parsed.netloc:
                return True
            return False

        # --- Navigate/Open with expected URL ---
        if skill in ("open", "navigate") and expected_url:
            if _url_matches(expected_url, after_url):
                return _build_verification_result(
                    is_success=True,
                    is_done=False,
                    summary=f"page action succeeded: {skill} -> {after_url[:80]}",
                    source="verifier",
                    failure_scope="local",
                    evidence=_compact_result_evidence(result),
                    decision_source="url_match",
                )
            return None

        # --- Click with expected URL ---
        if skill == "click" and expected_url:
            if _url_matches(expected_url, after_url):
                return _build_verification_result(
                    is_success=True,
                    is_done=False,
                    summary=f"click action succeeded: navigated to {after_url[:80]}",
                    source="verifier",
                    failure_scope="local",
                    evidence=_compact_result_evidence(result),
                    decision_source="url_match",
                )
            return None

        # --- Click without expected URL: check URL change signal ---
        if skill == "click" and not expected_url:
            page_transition_keywords = [
                "navigate", "open", "go to", "visit", "redirect",
                "\u8fdb\u5165", "\u8df3\u8f6c", "\u6253\u5f00", "\u8bbf\u95ee",
            ]
            has_transition_intent = any(kw in step_intent for kw in page_transition_keywords)
            url_changed = bool(execution_evidence.get("url_changed"))
            if url_changed and has_transition_intent:
                return _build_verification_result(
                    is_success=True,
                    is_done=False,
                    summary=f"click action (url changed): {skill}",
                    source="verifier",
                    failure_scope="local",
                    evidence=_compact_result_evidence(result),
                    confidence=0.75,
                    needs_llm=True,
                    decision_source="dpcli_page_url_change",
                )
            return None

        # --- Scroll/Wait: passive actions, tentative success ---
        if skill in ("scroll", "wait"):
            return _build_verification_result(
                is_success=True,
                is_done=False,
                summary=f"page action (tentative): {skill}",
                source="verifier",
                failure_scope="local",
                evidence=_compact_result_evidence(result),
                confidence=0.8,
                needs_llm=True,
                decision_source="dpcli_page_passive",
                warnings=["scroll/wait cannot be deterministically verified"],
            )

        # --- Type/Select: check target confidence ---
        if skill in ("type", "select"):
            target_confidence, ref_match, target_status = _check_target_confidence(state)
            if (target_confidence >= VERIFIER_MIN_TARGET_CONFIDENCE
                    and target_status == "selected"):
                return _build_verification_result(
                    is_success=True,
                    is_done=False,
                    summary=f"page action (target confidence {target_confidence:.0%}): {skill}",
                    source="verifier",
                    failure_scope="local",
                    evidence=_compact_result_evidence(result),
                    confidence=0.7,
                    needs_llm=True,
                    decision_source="target_confidence",
                )
            return None

        # Unknown page action → fall through to LLM
        return None

    return None


def _verify_dpcli_action_deterministically(state):
    """Backward-compatible wrapper: delegates to signal-enhanced verification.

    Returns a verification_result dict, or None to fall through to LLM verifier.
    """
    return _verify_dpcli_action_with_signals(state, state.get("current_url", ""))


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
        deterministic = _verify_dpcli_action_with_signals(state, current_url)
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