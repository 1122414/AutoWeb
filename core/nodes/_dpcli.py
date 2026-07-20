from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


from langgraph.types import Command

from core.state_v2 import AgentState
from core.nodes._verification import _build_verification_result
from skills.logger import logger


def _dpcli_action_kind(action: Optional[Dict[str, Any]] = None) -> str:
    """Classify a dp_cli action as observation, data, or page.

    observation: snapshot, expand, resolve-locator, find, session.inspect
                 (improve agent's visible context, no page effect expected)
    data: extract, list-items, batch-detail-extract
          (produce structured data output)
    page: open, navigate, click, type, scroll, wait
          (change browser state or page content)
    """
    skill = str((action or {}).get("skill") or "").strip().lower()
    if skill in {"snapshot", "expand", "resolve-locator", "find",
                 "session.inspect", "session_inspect"}:
        return "observation"
    if skill in {"extract", "list-items", "batch-detail-extract"}:
        return "data"
    if skill in {"open", "navigate", "click", "type", "scroll", "wait"}:
        return "page"
    return "unknown"


def _compact_result_evidence(result: Dict[str, Any]) -> Dict[str, Any]:
    """Extract compact evidence from a dp_cli result for verifier context."""
    evidence: Dict[str, Any] = {"ok": result.get("ok")}
    data = result.get("data") or {}
    if isinstance(data, dict):
        page = data.get("page") or {}
        evidence["url"] = page.get("url", "")
        stats = (data.get("index") or {}).get("stats") or {}
        if stats:
            evidence["node_count"] = stats.get("total_nodes")
        regions = data.get("index", {}).get("data_regions")
        if regions:
            evidence["data_regions"] = len(regions)
        items = data.get("items")
        if isinstance(items, list):
            evidence["item_count"] = len(items)
    return evidence


def _compact_dpcli_result_for_log(result: Dict[str, Any]) -> Dict[str, Any]:
    """Keep execution messages small while preserving the full result in state/output."""
    payload: Dict[str, Any] = {
        "ok": result.get("ok"),
        "session": result.get("session"),
        "action": result.get("action"),
        "error": result.get("error"),
        "evidence": _compact_result_evidence(result),
    }
    data = result.get("data") or {}
    if isinstance(data, dict):
        page = data.get("page")
        if isinstance(page, dict):
            payload["page"] = {
                "url": page.get("url", ""),
                "title": page.get("title", ""),
            }
        items = data.get("items")
        if isinstance(items, list):
            payload["item_samples"] = [
                {
                    key: item.get(key)
                    for key in (
                        "title",
                        "url",
                        "requested_url",
                        "final_url",
                        "detail_ok",
                        "detail_error",
                    )
                    if isinstance(item, dict) and item.get(key) not in (None, "")
                }
                for item in items[:3]
                if isinstance(item, dict)
            ]
            payload["items_omitted"] = max(0, len(items) - 3)
    return payload


def _compact_dpcli_snapshot(snapshot: Dict[str, Any], last_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = snapshot.get("data") if isinstance(snapshot, dict) else {}
    if not isinstance(data, dict):
        return {"error": "invalid dp_cli snapshot"}
    index = data.get("index") if isinstance(data.get("index"), dict) else {}
    return {
        "page": data.get("page") or {},
        "page_identity": data.get("page_identity") or {},
        "interactable_elements": (index.get("interactable_elements") or [])[:30],
        "data_regions": (index.get("data_regions") or [])[:5],
        "surface_index": (index.get("surface_index") or [])[:40],
        "stats": index.get("stats") or {},
        "delta": data.get("delta") or {},
        "last_result": (
            _compact_dpcli_result_for_log(last_result)
            if isinstance(last_result, dict)
            else None
        ),
    }


def _render_dpcli_snapshot_text(view: Dict[str, Any]) -> str:
    return json.dumps({
        "source": "dp_cli_snapshot",
        "page": view.get("page"),
        "page_identity": view.get("page_identity"),
        "interactable_elements": view.get("interactable_elements"),
        "data_regions": view.get("data_regions"),
        "surface_index": view.get("surface_index"),
        "stats": view.get("stats"),
        "delta": view.get("delta"),
        "last_result": view.get("last_result"),
    }, ensure_ascii=False, indent=2)


def _observer_dpcli_snapshot(state: AgentState) -> Optional[Command]:
    from config import (
        DPCLI_HEADLESS,
        DPCLI_OBSERVER_ENABLED,
        DPCLI_OBSERVER_FALLBACK_TO_DOM,
        DPCLI_SESSION,
        DPCLI_FULL_SNAPSHOT_MODE,
    )
    dpcli_execution = (
        state.get("execution_mode") == "dp_cli"
        or bool(state.get("dpcli_result"))
    )
    should_use_dpcli_observer = DPCLI_OBSERVER_ENABLED or dpcli_execution
    if not should_use_dpcli_observer:
        return None

    last_result = state.get("dpcli_result") or {}
    last_action = str(last_result.get("action") or "").strip().lower()
    if (
        last_result.get("ok")
        and last_action in {"extract", "list-items"}
        and state.get("dpcli_agent_view")
        and state.get("dpcli_snapshot_ref")
    ):
        agent_view = state.get("dpcli_agent_view") or {}
        text = json.dumps(agent_view, ensure_ascii=False, separators=(",", ":"))
        return Command(
            update={
                "_cache_failed_this_round": False,
                "_error_recovery_count": 0,
                "_last_recovery_error": None,
                "_observer_source": "dp_cli_reuse",
                "_dom_cache_hit_id": None,
                "dom_skeleton": text,
                "dom_hash": hashlib.md5(text.encode()).hexdigest(),
                "current_url": str(state.get("current_url") or ""),
            },
            goto="Planner",
        )

    from skills.dpcli_executor import DPCLIExecutor

    session = state.get("dpcli_session") or DPCLI_SESSION
    if (
        last_result.get("ok")
        and last_action == "snapshot"
        and isinstance((last_result.get("data") or {}).get("index"), dict)
    ):
        result = last_result
    else:
        result = DPCLIExecutor(session=session, headless=DPCLI_HEADLESS).snapshot(
            mode="agent_summary"
        )
    if result.get("ok"):
        if DPCLI_FULL_SNAPSHOT_MODE:
            try:
                return _build_full_snapshot_command(state, result, session)
            except Exception as full_err:
                import traceback
                logger.info(
                    f"   ⚠️ [Observer] full snapshot 构建失败，降级到 legacy 视图: {full_err}"
                )
                legacy_cmd = _build_legacy_snapshot_command(state, result, session)
                diagnostics_db = state.get("dpcli_observer_diagnostics") or {}
                errors = list(diagnostics_db.get("errors", []))
                errors.append({
                    "stage": "full_snapshot_build",
                    "error": str(full_err),
                    "traceback": traceback.format_exc()[-500:],
                })
                diagnostics_db["errors"] = errors
                legacy_update = legacy_cmd.update or {}
                legacy_update["dpcli_observer_diagnostics"] = diagnostics_db
                return Command(update=legacy_update, goto="Planner")
        return _build_legacy_snapshot_command(state, result, session)

    error = _dpcli_error(result)
    logger.info(f"   ⚠️ [Observer] dp_cli snapshot failed: {error}")
    if DPCLI_OBSERVER_FALLBACK_TO_DOM and not dpcli_execution:
        return None

    return Command(
        update={
            "_observer_source": "dp_cli",
            "dpcli_result": result,
            "verification_result": _build_verification_result(
                is_success=False,
                is_done=False,
                summary="dp_cli snapshot failed",
                source="executor",
                failure_scope="global",
                evidence=json.dumps(error, ensure_ascii=False),
                fix_hint="检查 dp_cli 浏览器会话或关闭 DPCLI_OBSERVER_ENABLED 回退旧 Observer",
            ),
            "error": str(error.get("message") or error.get("code") or "dp_cli snapshot failed"),
            "error_type": f"dpcli_{error.get('code') or 'snapshot_failed'}",
        },
        goto="ErrorHandler",
    )


def _build_legacy_snapshot_command(
    state: AgentState, result: Dict[str, Any], session: str
) -> Command:
    view = _compact_dpcli_snapshot(result, state.get("dpcli_result"))
    page = view.get("page") or {}
    text = _render_dpcli_snapshot_text(view)
    return Command(
        update={
            "_cache_failed_this_round": False,
            "_error_recovery_count": 0,
            "_last_recovery_error": None,
            "_observer_source": "dp_cli",
            "_dom_cache_hit_id": None,
            "dpcli_session": session,
            "dpcli_snapshot": result,
            "dpcli_snapshot_view": view,
            "dpcli_snapshot_delta": view.get("delta") or {},
            "dom_skeleton": text,
            "dom_hash": hashlib.md5(text.encode()).hexdigest(),
            "current_url": str(page.get("url") or state.get("current_url", "")),
        },
        goto="Planner",
    )


def _build_full_snapshot_command(
    state: AgentState, result: Dict[str, Any], session: str
) -> Command:
    from skills.dpcli_snapshot_store import SnapshotStore
    from skills.dpcli_snapshot_indexer import SnapshotIndexer
    from skills.dpcli_planner_view import PlannerViewGenerator

    store = SnapshotStore(session=session)
    indexer = SnapshotIndexer()
    view_gen = PlannerViewGenerator()

    snapshot_ref = store.save_full(result)
    snapshot_id = snapshot_ref["snapshot_id"]

    all_nodes = _collect_all_nodes(result)
    index_data = indexer.build_index(result)
    compressed_groups = indexer.build_compressed_index(all_nodes)
    agent_view = view_gen.generate(result, compressed_groups)
    diagnostics = view_gen.generate_diagnostics(result, compressed_groups)
    delta = result.get("data", {}).get("delta") or {}
    agent_view["delta"] = delta

    store.save_index(snapshot_id, index_data)
    store.save_compressed_index(snapshot_id, {
        "groups": compressed_groups,
        "uncompressed_count": len(all_nodes) - sum(g.get("count", 0) for g in compressed_groups),
    })
    store.save_planner_view(snapshot_id, agent_view)

    index_summary = _build_index_summary(index_data, store.session_dir, snapshot_id)
    text = json.dumps(agent_view, ensure_ascii=False, indent=2)
    page = result.get("data", {}).get("page", {})

    return Command(
        update={
            "_cache_failed_this_round": False,
            "_error_recovery_count": 0,
            "_last_recovery_error": None,
            "_observer_source": "dp_cli_full",
            "_dom_cache_hit_id": None,
            "dpcli_session": session,
            "dpcli_snapshot": {
                "ok": result.get("ok"),
                "session": result.get("session"),
                "action": result.get("action"),
                "data": {
                    "page": result.get("data", {}).get("page", {}),
                    "page_identity": result.get("data", {}).get("page_identity", {}),
                    "delta": delta,
                    "index": {
                        "stats": result.get("data", {}).get("index", {}).get("stats", {}),
                        "data_regions": result.get("data", {}).get("index", {}).get("data_regions", [])[:5],
                    },
                },
            },
            "dpcli_snapshot_view": agent_view,
            "dpcli_snapshot_ref": snapshot_ref,
            "dpcli_agent_view": agent_view,
            "dpcli_snapshot_index": index_summary,
            "dpcli_snapshot_delta": delta,
            "dpcli_observer_diagnostics": diagnostics,
            "dom_skeleton": text,
            "dom_hash": hashlib.md5(text.encode()).hexdigest(),
            "current_url": str(page.get("url") or state.get("current_url", "")),
        },
        goto="Planner",
    )


def _collect_all_nodes(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    from skills.dpcli_snapshot_indexer import SnapshotIndexer

    data = result.get("data") or {}
    idx = data.get("index") or {}
    raw_nodes = (
        list(idx.get("interactable_elements") or [])
        + list(idx.get("surface_index") or [])
        + list(idx.get("deep_index") or [])
    )
    merged_by_ref: Dict[str, Dict[str, Any]] = {}
    nodes_without_ref: List[Dict[str, Any]] = []
    for node in raw_nodes:
        if not isinstance(node, dict):
            continue
        ref = str(node.get("ref") or "")
        if not ref:
            nodes_without_ref.append(dict(node))
            continue
        existing = merged_by_ref.get(ref)
        merged_by_ref[ref] = (
            SnapshotIndexer._merge_node_info(existing, node)
            if existing
            else dict(node)
        )
    return nodes_without_ref + list(merged_by_ref.values())


def _build_index_summary(
    index_data: Dict[str, Any], session_dir: Any, snapshot_id: str
) -> Dict[str, Any]:
    summary = index_data.get("summary", {})
    return {
        "snapshot_id": snapshot_id,
        "full_snapshot_file": str(session_dir / f"{snapshot_id}.full.json"),
        "index_file": str(session_dir / f"{snapshot_id}.index.json"),
        "compressed_index_file": str(session_dir / f"{snapshot_id}.compressed_index.json"),
        "lookup_manifest": {
            "by_ref": True,
            "by_role": bool(index_data.get("by_role")),
            "by_text": bool(index_data.get("by_text")),
            "by_region": bool(index_data.get("by_region")),
            "by_tag": bool(index_data.get("by_tag")),
            "by_structural_group": bool(index_data.get("by_parent")),
        },
        "summary": summary,
        "top_level_groups": index_data.get("regions", [])[:10] if index_data.get("regions") else [],
    }


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    content = str(text or "").strip()
    if not content:
        return None
    if "```" in content:
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```",
                           content, flags=re.DOTALL)
        if fenced:
            content = fenced.group(1).strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            return None
    return parsed


def _should_use_dpcli_action(state: AgentState) -> bool:
    from config import DPCLI_ENABLED
    execution_mode = state.get("execution_mode")
    return bool(
        execution_mode == "dp_cli"
        or (DPCLI_ENABLED and execution_mode != "python_code")
    )


def _dpcli_action_context(state: AgentState) -> str:
    url = state.get("current_url", "")
    task = state.get("user_task", "")
    plan = state.get("plan", "")

    parts = [
        f"【当前页面】\nURL: {url}",
        f"【用户任务】\n{task}",
        f"【当前计划】\n{plan}",
    ]

    structured_plan = state.get("dpcli_structured_plan") or {}
    if structured_plan:
        action_payload = structured_plan.get("action_payload") or {}
        target_request = structured_plan.get("target_request") or {}
        parts.append(
            "【结构化计划】\n"
            f"  step_intent: {structured_plan.get('step_intent', '')}\n"
            f"  reason: {structured_plan.get('reason', '')}\n"
            f"  target_hint: {target_request.get('target_hint', '')}\n"
            f"  action_text: {action_payload.get('text', '')}\n"
            f"  action_url: {action_payload.get('url', '')}\n"
            f"  action_payload: {json.dumps(action_payload, ensure_ascii=False)}"
        )

    target_result = state.get("dpcli_target_result") or {}
    if target_result:
        status = target_result.get("status", "unknown")
        target_ref = target_result.get("target_ref", "")
        confidence = target_result.get("confidence", 0)
        evidence = target_result.get("evidence") or {}
        parts.append(
            "【目标匹配结果】\n"
            f"  status: {status}\n"
            f"  target_ref: {target_ref}\n"
            f"  confidence: {confidence}\n"
            f"  role: {evidence.get('role', '')}\n"
            f"  name: {evidence.get('name', '')}\n"
            f"  text: {evidence.get('text', '')}"
        )
        if target_result.get("approval_required"):
            parts.append(
                f"  ⚠️ 需要人工审批: {target_result.get('approval_reason', '')}"
            )

    return "\n\n".join(parts)


def _state_has_dpcli_refs(state: Optional[AgentState]) -> bool:
    if not state:
        return False
    if state.get("dpcli_snapshot_ref"):
        return True
    snapshot = state.get("dpcli_snapshot") or {}
    index = snapshot.get("data", {}).get("index") if isinstance(snapshot, dict) else None
    return bool(index and isinstance(index, dict))


def _validate_dpcli_action(action: Dict[str, Any], state: Optional[AgentState] = None) -> Optional[str]:
    skill = str(action.get("skill", "")).strip().lower()
    params = action.get("params") or {}

    if not skill:
        return "missing skill"
    if not isinstance(params, dict):
        return "params must be an object"

    required = {
        "click": ["ref", "locator", "target_ref"],
        "type": ["ref", "locator", "target_ref"],
        "select": ["ref", "locator", "target_ref"],
        "find": ["text", "ref", "locator"],
        "expand": ["ref", "locator"],
        "list-items": ["group_ref", "ref", "locator", "target_ref"],
    }

    if skill in required:
        has_any = any(bool(params.get(k)) for k in required[skill])
        if not has_any:
            return f"{skill} requires ref or locator"

    executable_ref = (
        params.get("ref")
        or params.get("target_ref")
        or params.get("group_ref")
    )
    if executable_ref and str(executable_ref).startswith("g_"):
        return (
            f"{skill} cannot execute virtual group ref '{executable_ref}'; "
            "use a concrete r*/e* snapshot ref"
        )

    if skill == "click" and state:
        if _state_has_dpcli_refs(state) and params.get("locator"):
            return "click must use a snapshot ref instead of a free-form locator"

    if skill in ("click", "type", "select") and state:
        target_result = state.get("dpcli_target_result") or {}
        structured_plan = state.get("dpcli_structured_plan") or {}
        target_required = (
            structured_plan.get("target_request", {}).get("required", False)
            if isinstance(structured_plan.get("target_request"), dict)
            else False
        )

        if target_required:
            if target_result.get("status") != "selected":
                return (
                    f"{skill} requires a selected target but TargetSelector status is "
                    f"'{target_result.get('status', 'unknown')}'"
                )

            expected_ref = target_result.get("target_ref")
            if expected_ref:
                action_ref = params.get("ref") or params.get("target_ref")
                if not action_ref:
                    return f"{skill} requires ref/target_ref but none provided"
                if action_ref != expected_ref:
                    return (
                        f"target ref mismatch: action uses '{action_ref}' "
                        f"but TargetSelector selected '{expected_ref}'"
                    )

            if params.get("locator") and not params.get("ref") and not params.get("target_ref"):
                return f"{skill} must use target_ref from TargetSelector, not free-form locator"

    return None


def _dpcli_result_url(result: Dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return ""
    data = result.get("data") or {}
    page = data.get("page") or {}
    return str(page.get("url") or "")


def _dpcli_error(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {"code": "invalid_result", "message": "result is not dict"}
    error = result.get("error")
    if isinstance(error, dict):
        return error
    if error:
        return {"code": "unknown", "message": str(error)}
    return {"code": "unknown", "message": "unknown error"}


def _dpcli_failure_goto(error_code: str) -> str:
    mapping = {
        "ref_stale": "Observer",
        "invalid_action": "Coder",
        "execution_failed": "Observer",
        "timeout": "Observer",
        "snapshot_failed": "Observer",
    }
    return mapping.get(error_code, "Observer")


def _dpcli_snapshot_loop_fallback_plan(
    state: AgentState, structured_plan: Dict[str, Any]
) -> Dict[str, Any]:
    """Rewrite redundant observation/data-target plans to recoverable data actions.

    Observer already refreshes dp_cli snapshots before Planner. If Planner asks
    for another snapshot while a snapshot ref/view is available, executing that
    action only repeats the Observer work and can trap the graph in an
    Observer->Planner->Coder->Executor(snapshot) loop.

    Data collection has a related trap: the planner view already exposes
    concrete data region refs (r*) with extract/list-items capabilities, but the
    Planner may still ask TargetSelector to rediscover that same region. On
    pages with duplicated or very similar regions, TargetSelector can return
    need_approval/not_found forever. For data intents, prefer an available data
    region from the planner view and let Coder emit a policy action directly.
    """
    if not isinstance(structured_plan, dict):
        return structured_plan

    intent = str(structured_plan.get("step_intent") or "").strip().lower()
    if not (state.get("dpcli_snapshot_ref") or state.get("dpcli_agent_view")):
        return structured_plan

    should_recover = intent == "snapshot"
    if intent in {"extract", "list-items", "expand"}:
        target_request = (
            structured_plan.get("target_request")
            if isinstance(structured_plan.get("target_request"), dict)
            else {}
        )
        target_result = state.get("dpcli_target_result") or {}
        should_recover = bool(target_request.get("required")) or target_result.get(
            "status"
        ) in {"not_found", "need_approval"}

    if not should_recover:
        return structured_plan

    candidate = _dpcli_recoverable_data_candidate(state, structured_plan)
    if not candidate:
        return structured_plan

    rewritten = dict(structured_plan)
    rewritten["step_intent"] = candidate["intent"]
    rewritten["target_request"] = {"required": False}
    rewritten["action_payload"] = candidate["params"]
    rewritten["reason"] = (
        f"{structured_plan.get('reason', '')} "
        "Snapshot was already refreshed by Observer; continue with recoverable "
        f"{candidate['intent']} on {candidate['ref']}."
    ).strip()
    rewritten["_planner_rewrite"] = (
        "snapshot_loop_guard" if intent == "snapshot" else "data_region_direct"
    )
    return rewritten


def _dpcli_recoverable_data_candidate(
    state: AgentState, structured_plan: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    agent_view = state.get("dpcli_agent_view") or {}
    capability_map = agent_view.get("capability_map") or {}
    structured_plan = structured_plan or {}
    intent = str(structured_plan.get("step_intent") or "").strip().lower()
    target_request = (
        structured_plan.get("target_request")
        if isinstance(structured_plan.get("target_request"), dict)
        else {}
    )
    action_payload = (
        structured_plan.get("action_payload")
        if isinstance(structured_plan.get("action_payload"), dict)
        else {}
    )
    task_contract = state.get("dpcli_task_contract") or {}
    extract_schema = (
        action_payload.get("schema")
        or task_contract.get("schema")
        or ["title", "url"]
    )
    extract_limit = action_payload.get("limit")
    if extract_limit is None:
        extract_limit = task_contract.get("per_page_limit") or 20

    regions = list(capability_map.get("data_regions") or [])
    ranked_regions = sorted(
        (
            (_dpcli_region_candidate_score(region, intent, target_request), region)
            for region in regions
            if isinstance(region, dict)
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    if len(ranked_regions) > 1:
        top_score, top_region = ranked_regions[0]
        second_score, _ = ranked_regions[1]
        hint_values = [
            str(target_request.get(key) or "").strip().lower()
            for key in ("target_hint", "region_hint")
        ]
        explicit_top_ref = str(top_region.get("ref") or "").lower() in hint_values
        if not explicit_top_ref and top_score - second_score < 15:
            return None

    for _score, region in ranked_regions:
        ref = str(region.get("ref") or "")
        if not re.fullmatch(r"r\d+", ref):
            continue
        actions = set(region.get("available_actions") or [])
        if intent == "list-items" and "list-items" in actions:
            return {
                "intent": "list-items",
                "ref": ref,
                "params": {
                    "group_ref": ref,
                    "sample_size": int(action_payload.get("sample_size") or 10),
                },
            }
        if intent == "expand" and "expand" in actions:
            return {
                "intent": "expand",
                "ref": ref,
                "params": {"ref": ref, "depth": 2},
            }
        if (
            intent in ("", "snapshot", "extract")
            or intent not in {"list-items", "expand"}
        ) and "extract" in actions:
            return {
                "intent": "extract",
                "ref": ref,
                "params": {
                    "target_ref": ref,
                    "schema": list(extract_schema),
                    "limit": int(extract_limit),
                },
            }
        if "list-items" in actions:
            return {
                "intent": "list-items",
                "ref": ref,
                "params": {"group_ref": ref, "sample_size": 10},
            }
        if "expand" in actions:
            return {
                "intent": "expand",
                "ref": ref,
                "params": {"ref": ref, "depth": 2},
            }

    for group in agent_view.get("top_level_groups") or []:
        ref = str(group.get("region_ref") or "")
        if not re.fullmatch(r"r\d+", ref):
            continue
        return {
            "intent": "list-items",
            "ref": ref,
            "params": {"group_ref": ref, "sample_size": 10},
        }

    return None


def _dpcli_region_candidate_score(
    region: Dict[str, Any], intent: str, target_request: Dict[str, Any]
) -> int:
    """Score planner-view data regions for direct data actions."""
    score = 0
    actions = set(region.get("available_actions") or [])
    if intent == "extract" and "extract" in actions:
        score += 50
    elif intent == "list-items" and "list-items" in actions:
        score += 50
    elif intent == "expand" and "expand" in actions:
        score += 50
    elif "extract" in actions:
        score += 20

    ref = str(region.get("ref") or "")
    name = str(region.get("name") or "")
    kind = str(region.get("kind") or "")
    text_blob = " ".join(
        [
            ref,
            name,
            kind,
            " ".join(
                str(s.get("text") or "")
                for s in region.get("samples") or []
                if isinstance(s, dict)
            ),
        ]
    ).lower()

    hints = []
    for key in ("target_hint", "region_hint"):
        value = target_request.get(key)
        if value:
            hints.append(str(value))
    text_or_name = target_request.get("text_or_name")
    if isinstance(text_or_name, list):
        hints.extend(str(v) for v in text_or_name if v)
    elif text_or_name:
        hints.append(str(text_or_name))

    constraints = target_request.get("constraints") or {}
    if isinstance(constraints, dict):
        for key in ("name", "name_contains", "kind", "region_hint"):
            value = constraints.get(key)
            if value:
                hints.append(str(value))
        if constraints.get("item_count") == region.get("item_count"):
            score += 10
        if constraints.get("kind") and str(constraints.get("kind")) == kind:
            score += 15

    for hint in hints:
        hint_text = str(hint).strip().lower()
        if not hint_text:
            continue
        if hint_text == ref.lower():
            score += 100
        elif hint_text in text_blob:
            score += 30
        elif any(token and token in text_blob for token in hint_text.split()):
            score += 10

    try:
        score += min(int(region.get("item_count") or 0), 50)
    except (TypeError, ValueError):
        pass
    try:
        score += min(max(int(region.get("source_score") or 0), 0) // 20, 30)
    except (TypeError, ValueError):
        pass
    return score


def _dpcli_recoverable_group_from_snapshot_ref(
    state: AgentState,
) -> Optional[Dict[str, Any]]:
    snapshot_ref = state.get("dpcli_snapshot_ref") or {}
    compressed_file = snapshot_ref.get("compressed_index_file")
    if not compressed_file:
        return None
    try:
        path = Path(str(compressed_file))
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.info(f"   [Planner-dp_cli] compressed group fallback unavailable: {exc}")
        return None

    groups = payload.get("groups") if isinstance(payload, dict) else None
    if not isinstance(groups, list):
        return None
    # Compressed g_* identifiers describe index groups, not DOM nodes. They
    # are intentionally never converted into executable refs.
    return None


def _dpcli_policy_action_from_structured_plan(
    state: AgentState,
) -> Optional[Dict[str, Any]]:
    structured_plan = state.get("dpcli_structured_plan") or {}
    is_planner_rewrite = structured_plan.get("_planner_rewrite") in {
        "snapshot_loop_guard",
        "data_region_direct",
    }
    if not is_planner_rewrite and structured_plan.get("_contract_action") is not True:
        return None

    intent = str(structured_plan.get("step_intent") or "").strip().lower()
    payload = structured_plan.get("action_payload") or {}
    if not isinstance(payload, dict):
        return None

    reason = str(structured_plan.get("reason") or "deterministic dp_cli plan")
    if intent in {"open", "navigate"}:
        url = payload.get("url")
        if url:
            return {
                "skill": "open",
                "params": {"url": str(url)},
                "reason": reason,
            }
    if intent == "click":
        ref = payload.get("ref") or payload.get("target_ref")
        locator = payload.get("locator")
        if ref or locator:
            params: Dict[str, Any] = {}
            if ref:
                params["ref"] = str(ref)
            if locator:
                params["locator"] = str(locator)
            if payload.get("wait_time") is not None:
                params["wait_time"] = payload.get("wait_time")
            return {"skill": "click", "params": params, "reason": reason}
    if intent == "type":
        ref = payload.get("ref") or payload.get("target_ref")
        locator = payload.get("locator")
        text = payload.get("text")
        if (ref or locator) and text is not None:
            params = {"text": str(text)}
            if ref:
                params["ref"] = str(ref)
            if locator:
                params["locator"] = str(locator)
            if payload.get("submit") is not None:
                params["submit"] = bool(payload.get("submit"))
            if payload.get("wait_time") is not None:
                params["wait_time"] = payload.get("wait_time")
            return {"skill": "type", "params": params, "reason": reason}
    if intent == "scroll":
        params = {
            "direction": str(payload.get("direction") or "down"),
            "amount": int(payload.get("amount") or 900),
        }
        if payload.get("to"):
            params["to"] = str(payload.get("to"))
        if payload.get("wait_time") is not None:
            params["wait_time"] = payload.get("wait_time")
        return {"skill": "scroll", "params": params, "reason": reason}
    if intent == "wait":
        seconds = payload.get("seconds")
        if seconds is None and payload.get("timeout_ms") is not None:
            seconds = float(payload["timeout_ms"]) / 1000.0
        return {
            "skill": "wait",
            "params": {"seconds": float(seconds if seconds is not None else 1.0)},
            "reason": reason,
        }
    if intent == "list-items":
        group_ref = payload.get("group_ref") or payload.get("ref") or payload.get("target_ref")
        if group_ref and not str(group_ref).startswith("g_"):
            return {
                "skill": "list-items",
                "params": {
                    "group_ref": str(group_ref),
                    "sample_size": int(payload.get("sample_size") or 10),
                },
                "reason": reason,
            }
    if intent == "extract":
        target_ref = payload.get("target_ref") or payload.get("ref") or payload.get("group_ref")
        if target_ref and not str(target_ref).startswith("g_"):
            action: Dict[str, Any] = {
                "skill": "extract",
                "params": {
                    "target_ref": str(target_ref),
                    "schema": payload.get("schema") or ["title", "url"],
                },
                "reason": reason,
            }
            if payload.get("limit") is not None:
                action["params"]["limit"] = payload.get("limit")
            return action
    if intent == "expand":
        ref = payload.get("ref") or payload.get("target_ref") or payload.get("group_ref")
        if ref and not str(ref).startswith("g_"):
            return {
                "skill": "expand",
                "params": {"ref": str(ref), "depth": int(payload.get("depth") or 2)},
                "reason": reason,
            }
    return None


def _dpcli_planner_context(state: AgentState) -> str:
    from prompts.dpcli_planner_prompts import DPCLI_PLANNER_PROMPT
    from core.nodes._context import _prune_finished_steps

    agent_view = state.get("dpcli_agent_view")
    if not agent_view:
        return ""

    try:
        import json
        view_text = json.dumps(
            agent_view,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except Exception:
        view_text = str(agent_view)

    task = str(state.get("user_task", ""))
    finished_steps = _prune_finished_steps(
        finished_steps=list(state.get("finished_steps", []) or []),
        prompt_text=view_text + task,
    )
    reflections = [
        str(item)[:500]
        for item in list(state.get("reflections", []) or [])[-5:]
    ]

    return DPCLI_PLANNER_PROMPT.format(
        agent_view=view_text,
        user_task=task,
        current_url=state.get("current_url", ""),
        finished_steps=finished_steps,
        reflections=json.dumps(reflections, ensure_ascii=False),
        loop_count=str(state.get("loop_count", 0)),
        execution_mode=state.get("execution_mode", "python_code"),
    )
