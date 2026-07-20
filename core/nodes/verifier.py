from __future__ import annotations

import difflib
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


def _contract_action_verification(state, skill: str):
    """Validate one data result against the original user task contract."""
    contract = state.get("dpcli_task_contract") or {}
    if not contract or skill not in {"extract", "list-items", "batch-detail-extract"}:
        return None

    from skills.dpcli_task_contract import evaluate_contract_items, result_items

    action = state.get("generated_action") or {}
    params = action.get("params") or {}
    expected_count = params.get("limit") or contract.get("per_page_limit") or 1
    items = result_items(state.get("dpcli_result") or {})
    evaluation_contract = contract
    if skill in {"extract", "list-items"} and contract.get("detail_required"):
        evaluation_contract = dict(contract)
        evaluation_contract["schema"] = list(
            contract.get("list_schema") or ["title", "url"]
        )
    evaluation = evaluate_contract_items(
        evaluation_contract,
        items,
        # A data region may legitimately contain only part of the requested
        # page. Validate its schema here, then let cumulative progress decide
        # whether the overall task is complete.
        expected_count=1,
    )
    item_count = int(evaluation["item_count"])
    requested_count = int(expected_count)
    is_success = bool(evaluation["is_success"])
    is_partial = is_success and item_count < requested_count
    if is_partial:
        summary = (
            f"partial task-contract data "
            f"({item_count}/{requested_count} items; cumulative progress)"
        )
    elif is_success:
        summary = f"task-contract data valid ({item_count} items)"
    else:
        summary = evaluation["summary"]
    return _build_verification_result(
        is_success=is_success,
        is_done=False,
        summary=summary,
        source="verifier",
        failure_scope="local",
        evidence=str(
            {
                "item_count": evaluation["item_count"],
                "required_count": requested_count,
                "field_coverage": evaluation["field_coverage"],
            }
        ),
        fix_hint=(
            "continue with task-contract progress"
            if is_success
            else "select another concrete data region matching the original task schema"
        ),
        decision_source="task_contract",
    )


def _merge_dpcli_contract_progress(state):
    """Return progress, cumulative evaluation, and completion decision."""
    contract = state.get("dpcli_task_contract") or {}
    if not contract:
        return None

    from skills.dpcli_task_contract import (
        evaluate_contract_items,
        merge_contract_progress,
        result_items,
    )

    progress = state.get("dpcli_task_progress") or {}
    previous_item_count = len(progress.get("items") or [])
    page_number = max(1, int(progress.get("active_page") or 1))
    merged = merge_contract_progress(
        progress,
        result_items(state.get("dpcli_result") or {}),
        page_number=page_number,
    )
    skill = str((state.get("generated_action") or {}).get("skill") or "")
    is_list_action = skill in {"extract", "list-items"}
    evaluation_contract = contract
    if is_list_action and contract.get("detail_required"):
        evaluation_contract = dict(contract)
        evaluation_contract["schema"] = list(
            contract.get("list_schema") or ["title", "url"]
        )
    evaluation = evaluate_contract_items(
        evaluation_contract,
        merged.get("items") or [],
        expected_count=int(contract.get("min_items") or 1),
    )
    completed_pages = {
        int(value)
        for value in merged.get("completed_pages") or []
        if str(value).isdigit()
    }
    target_pages = max(1, int(contract.get("target_pages") or 1))
    pages_done = len(completed_pages) >= target_pages
    list_complete = bool(evaluation["is_success"] and pages_done)
    if is_list_action:
        merged["list_complete"] = list_complete
        if contract.get("collection_mode") == "infinite_scroll":
            current_item_count = len(merged.get("items") or [])
            merged["stagnant_rounds"] = (
                int(progress.get("stagnant_rounds") or 0) + 1
                if previous_item_count > 0 and current_item_count <= previous_item_count
                else 0
            )
    if skill == "batch-detail-extract":
        merged["detail_complete"] = bool(evaluation["is_success"] and pages_done)
    is_done = bool(
        evaluation["is_success"]
        and pages_done
        and (
            not contract.get("detail_required")
            or skill == "batch-detail-extract"
        )
    )
    return merged, evaluation, is_done


def _advance_contract_page_progress(state):
    """Apply page-action progress only after deterministic verification succeeds."""
    progress = dict(state.get("dpcli_task_progress") or {})
    plan = state.get("dpcli_structured_plan") or {}
    payload = plan.get("action_payload") or {}
    intent = str(plan.get("step_intent") or "").lower()

    if intent == "click" and payload.get("page_number") is not None:
        progress["active_page"] = max(1, int(payload["page_number"]))
        progress["failed_region_refs"] = []
    elif intent == "type" and payload.get("filter_stage") == "applied":
        progress["filter_applied"] = True
        progress["failed_region_refs"] = []
        progress["completed_pages"] = []
        progress["active_page"] = 1
    elif intent == "scroll":
        requested_round = int(payload.get("round") or 0)
        progress["scroll_round"] = max(
            int(progress.get("scroll_round") or 0),
            requested_round,
        )
        progress["failed_region_refs"] = []
    return progress


def _mark_contract_region_failed(state, progress_override=None):
    progress = dict(
        progress_override
        if progress_override is not None
        else state.get("dpcli_task_progress") or {}
    )
    failed_refs = list(progress.get("failed_region_refs") or [])
    plan = state.get("dpcli_structured_plan") or {}
    payload = plan.get("action_payload") or {}
    ref = payload.get("target_ref") or payload.get("group_ref") or payload.get("ref")
    if ref and ref not in failed_refs:
        failed_refs.append(str(ref))
    progress["failed_region_refs"] = failed_refs
    return progress


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


def _is_meaningful_value(value) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _valid_http_url(value) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urllib.parse.urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _schema_item_view(item, skill: str) -> dict:
    if not isinstance(item, dict):
        return {}
    if skill != "batch-detail-extract":
        return item
    merged = {}
    list_info = item.get("list_info")
    detail_info = item.get("detail_info")
    if isinstance(list_info, dict):
        merged.update(list_info)
    if isinstance(detail_info, dict):
        merged.update(detail_info)
    return merged


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
        has_items = isinstance(items, list) and len(items) > 0

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

        contract_result = _contract_action_verification(state, skill)
        if contract_result is not None:
            return contract_result

        if skill == "batch-detail-extract":
            verified_detail_items = [
                item
                for item in items
                if isinstance(item, dict)
                and item.get("detail_ok") is True
                and _valid_http_url(item.get("final_url"))
                and isinstance(item.get("detail_info"), dict)
                and any(
                    _is_meaningful_value(value)
                    for value in item.get("detail_info", {}).values()
                )
            ]
            success_ratio = len(verified_detail_items) / len(items)
            if success_ratio < 0.8:
                return _build_verification_result(
                    is_success=False,
                    is_done=False,
                    summary=(
                        f"batch detail quality failed: {len(verified_detail_items)}/"
                        f"{len(items)} rows verified ({success_ratio:.0%})"
                    ),
                    source="verifier",
                    failure_scope="local",
                    evidence=_compact_result_evidence(result),
                    fix_hint=(
                        "filter invalid detail URLs and retry failed rows; require final_url "
                        "and meaningful detail fields"
                    ),
                    decision_source="batch_detail_quality",
                )
            items = verified_detail_items

        # Get schema from action params or structured_plan
        structured_plan = state.get("dpcli_structured_plan") or {}
        schema = (
            action.get("params", {}).get("schema")
            or structured_plan.get("action_payload", {}).get("schema")
        )

        usable_items = [
            item
            for item in items
            if isinstance(item, dict)
            and any(_is_meaningful_value(value) for value in item.values())
        ]
        if not usable_items:
            return _build_verification_result(
                is_success=False,
                is_done=False,
                summary=f"data action returned only empty item shells: {skill}",
                source="verifier",
                failure_scope="local",
                evidence=_compact_result_evidence(result),
                fix_hint="select a better data region and require non-empty field values",
                decision_source="data_quality",
            )

        # No schema defined → old behavior (items exist = success)
        if not schema or not isinstance(schema, list) or len(schema) == 0:
            return _build_verification_result(
                is_success=True,
                is_done=False,
                summary=f"data action succeeded: {skill} ({len(usable_items)} items)",
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
                summary=f"data action succeeded: {skill} ({len(usable_items)} items)",
                source="verifier",
                failure_scope="local",
                evidence=_compact_result_evidence(result),
                decision_source="dpcli_data",
            )

        populated_cells = 0
        total_cells = len(schema_fields) * len(usable_items)
        extracted_urls = []
        for item in usable_items:
            view = _schema_item_view(item, skill)
            normalized = {
                str(key).strip().lower(): value
                for key, value in view.items()
            }
            for field in schema_fields:
                value = normalized.get(field)
                if field in {"url", "href", "link", "detail_url", "final_url"}:
                    if _valid_http_url(value):
                        populated_cells += 1
                        extracted_urls.append(str(value).rstrip("/"))
                elif _is_meaningful_value(value):
                    populated_cells += 1

        coverage = populated_cells / total_cells if total_cells else 0.0
        if coverage == 0:
            return _build_verification_result(
                is_success=False,
                is_done=False,
                summary=f"data action produced no valid schema values: {skill}",
                source="verifier",
                failure_scope="local",
                evidence=_compact_result_evidence(result),
                fix_hint="retry extraction with a specific data region and valid http(s) URLs",
                decision_source="data_quality",
            )

        if len(extracted_urls) > 1:
            unique_ratio = len(set(extracted_urls)) / len(extracted_urls)
            if unique_ratio < 0.8:
                return _build_verification_result(
                    is_success=False,
                    is_done=False,
                    summary=f"data action returned duplicate URLs ({unique_ratio:.0%} unique): {skill}",
                    source="verifier",
                    failure_scope="local",
                    evidence=_compact_result_evidence(result),
                    fix_hint="deduplicate list items and reject repeated list-page URLs",
                    decision_source="data_quality",
                )

        if coverage >= VERIFIER_SCHEMA_COVERAGE_THRESHOLD:
            return _build_verification_result(
                is_success=True,
                is_done=False,
                summary=f"data action succeeded: {skill} ({len(usable_items)} items, value coverage {coverage:.0%})",
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
        is_contract_action = structured_plan.get("_contract_action") is True

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
            if is_contract_action:
                return _build_verification_result(
                    is_success=True,
                    is_done=False,
                    summary="task-contract click succeeded",
                    source="verifier",
                    failure_scope="local",
                    evidence=_compact_result_evidence(result),
                    decision_source="task_contract",
                )
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
                confidence=1.0 if is_contract_action else 0.8,
                needs_llm=not is_contract_action,
                decision_source=(
                    "task_contract" if is_contract_action else "dpcli_page_passive"
                ),
                warnings=(
                    [] if is_contract_action
                    else ["scroll/wait cannot be deterministically verified"]
                ),
            )

        # --- Type/Select: check target confidence ---
        if skill in ("type", "select"):
            if is_contract_action:
                return _build_verification_result(
                    is_success=True,
                    is_done=False,
                    summary=f"task-contract form action succeeded: {skill}",
                    source="verifier",
                    failure_scope="local",
                    evidence=_compact_result_evidence(result),
                    confidence=1.0,
                    needs_llm=False,
                    decision_source="task_contract",
                )
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
        warnings = list(updates.get("verification_result", {}).get("warnings") or [])
        warnings.append(f"ActionCache save skipped: {action_store_exc}")
        if "verification_result" not in updates:
            updates["verification_result"] = {}
        updates["verification_result"]["warnings"] = warnings
        curr_hint = str(updates.get("verification_result", {}).get("fix_hint", ""))
        updates["verification_result"]["fix_hint"] = curr_hint + " | ActionCache unavailable"

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
        warnings = list(updates.get("verification_result", {}).get("warnings") or [])
        warnings.append(f"Detail batch policy skipped: {policy_exc}")
        if "verification_result" not in updates:
            updates["verification_result"] = {}
        updates["verification_result"]["warnings"] = warnings

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


def _detect_duplicate_action(state):
    """P2-1: Detect when the same or highly similar action is being repeated.

    Uses difflib.SequenceMatcher to compare current action against recent
    finished_steps summaries. Returns a failure verification_result dict if a
    duplicate loop is detected, or None.
    """
    from config import VERIFIER_DUPLICATE_ACTION_THRESHOLD, VERIFIER_DUPLICATE_ACTION_MIN_COUNT
    import json
    finished = state.get("finished_steps") or []
    if len(finished) < VERIFIER_DUPLICATE_ACTION_MIN_COUNT:
        return None
    action = state.get("generated_action")
    current_action_str = json.dumps(action, sort_keys=True, ensure_ascii=False) if action else ""
    if not current_action_str:
        return None
    recent_summaries = finished[-VERIFIER_DUPLICATE_ACTION_MIN_COUNT:]
    matches = 0
    for summary in recent_summaries:
        ratio = difflib.SequenceMatcher(None, current_action_str, str(summary)).ratio()
        if ratio >= VERIFIER_DUPLICATE_ACTION_THRESHOLD:
            matches += 1
    if matches >= VERIFIER_DUPLICATE_ACTION_MIN_COUNT:
        return _build_verification_result(
            is_success=False, is_done=False,
            summary="Duplicate action detected — same or highly similar action repeated",
            source="verifier", failure_scope="global",
            decision_source="duplicate_action",
            fix_hint="Change strategy; current approach is looping on same action",
        )
    return None


def verifier_node(state: AgentState, config: RunnableConfig, llm) -> Command[Literal["Observer", "Planner", "Executor", "RAGNode"]]:
    """[Verifier] 验收并决定下一步"""
    logger.info("\n🔍 [Verifier] 正在验收...")

    log = state.get("execution_log", "")
    task = state.get("user_task", "")
    current_plan = state.get("plan", "Unknown Plan")
    code_source = state.get("_code_source", "llm")
    current_suggestions = state.get("locator_suggestions", [])

    # 获取最新标签页（处理新标签页打开的情况）
    is_dpcli_execution = state.get("execution_mode") == "dp_cli"
    dpcli_current_url = (
        _dpcli_result_url(state.get("dpcli_result") or {})
        if is_dpcli_execution
        else ""
    )

    browser = config["configurable"].get("browser")
    if is_dpcli_execution:
        tab = None
        current_url = dpcli_current_url or str(state.get("current_url") or "")
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

    # P2-1: Duplicate action detection (before keyword scan)
    duplicate_result = _detect_duplicate_action(state)
    if duplicate_result is not None:
        logger.info(f"   [Verifier] Duplicate action detected, returning to Planner")
        return Command(
            update={
                "messages": [AIMessage(content=f"Status: STEP_FAIL (duplicate_action)")],
                "reflections": [duplicate_result["summary"]],
                "verification_result": duplicate_result,
                "is_complete": False,
            },
            goto="Planner",
        )

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

                if (
                    action_kind == "page"
                    and (state.get("dpcli_structured_plan") or {}).get(
                        "_contract_action"
                    )
                ):
                    updates["dpcli_task_progress"] = (
                        _advance_contract_page_progress(state)
                    )

                if action_kind == "data":
                    contract_progress = _merge_dpcli_contract_progress(state)
                    if contract_progress is not None:
                        progress, cumulative, is_done = contract_progress
                        updates["dpcli_task_progress"] = progress
                        if is_done:
                            done_result = dict(deterministic)
                            done_result.update({
                                "is_done": True,
                                "summary": cumulative["summary"],
                                "decision_source": "task_contract",
                            })
                            updates.update({
                                "is_complete": True,
                                "verification_result": done_result,
                                "finished_steps": [cumulative["summary"]],
                            })
                            logger.info(
                                "   [Verifier] task contract satisfied, ending graph"
                            )
                            return Command(update=updates, goto="__end__")
                        # The region was valid but did not yet complete the
                        # cumulative contract. Exclude it for the rest of this
                        # page so Planner can collect another region instead
                        # of extracting the same partial rows forever.
                        updates["dpcli_task_progress"] = (
                            _mark_contract_region_failed(state, progress)
                        )

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

            if (
                _dpcli_action_kind(state.get("generated_action") or {}) == "data"
                and state.get("dpcli_task_contract")
            ):
                updates["dpcli_task_progress"] = _mark_contract_region_failed(state)
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

        # P2-2: _step_fail_count escalation
        from config import VERIFIER_FAIL_COUNT_GLOBAL_ESCALATE, VERIFIER_FAIL_COUNT_TERMINATE
        step_fail_count = state.get("_step_fail_count", 0)
        if step_fail_count >= VERIFIER_FAIL_COUNT_GLOBAL_ESCALATE:
            updates["verification_result"]["failure_scope"] = "global"
            warnings = list(updates.get("verification_result", {}).get("warnings") or [])
            warnings.append(f"Escalated to global after {step_fail_count} consecutive failures")
            updates["verification_result"]["warnings"] = warnings
        if step_fail_count >= VERIFIER_FAIL_COUNT_TERMINATE:
            curr_hint = str(updates.get("verification_result", {}).get("fix_hint", ""))
            updates["verification_result"]["fix_hint"] = curr_hint + " | Consider terminating task or requesting human intervention after {} failures".format(step_fail_count)

        # P2-3: 仅在 global 失败时回滚整条最新策略；local 失败保留上下文做定向修复
        if failure_scope == "global":
            if len(current_suggestions) > 1:
                updates["locator_suggestions"] = {"__replace__": current_suggestions[:-1]}
            elif len(current_suggestions) == 1:
                updates["reflections"] = (state.get("reflections") or []) + [
                    "Last locator suggestion exhausted; Observer must re-observe or Planner must change strategy"
                ]
            # empty list: do not write locator_suggestions at all

        # 缓存代码验收失败：失效缓存 + 跳 Planner
        if code_source == "cache":
            return _handle_cache_failure(state, updates)

        # LLM 代码失败：回 Observer 重试
        return Command(update=updates, goto="Observer")
