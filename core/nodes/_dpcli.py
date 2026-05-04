from __future__ import annotations

import hashlib
import json
import re
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
        "last_result": last_result,
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
        "last_result": view.get("last_result"),
    }, ensure_ascii=False, indent=2)


def _observer_dpcli_snapshot(state: AgentState) -> Optional[Command]:
    from config import (
        DPCLI_ENABLED,
        DPCLI_HEADLESS,
        DPCLI_OBSERVER_ENABLED,
        DPCLI_OBSERVER_FALLBACK_TO_DOM,
        DPCLI_SESSION,
        DPCLI_FULL_SNAPSHOT_MODE,
    )
    should_use_dpcli_observer = (
        DPCLI_ENABLED
        or DPCLI_OBSERVER_ENABLED
        or state.get("execution_mode") == "dp_cli"
        or bool(state.get("dpcli_result"))
    )
    if not should_use_dpcli_observer:
        return None

    from skills.dpcli_executor import DPCLIExecutor

    session = state.get("dpcli_session") or DPCLI_SESSION
    result = DPCLIExecutor(session=session, headless=DPCLI_HEADLESS).snapshot(
        mode="agent_summary")
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
    if DPCLI_OBSERVER_FALLBACK_TO_DOM:
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
            "_observer_source": "dp_cli",
            "_dom_cache_hit_id": None,
            "dpcli_session": session,
            "dpcli_snapshot": result,
            "dpcli_snapshot_view": view,
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
    agent_view = view_gen.generate(result, index_data, compressed_groups)
    diagnostics = view_gen.generate_diagnostics(result, compressed_groups)

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
            "_observer_source": "dp_cli_full",
            "_dom_cache_hit_id": None,
            "dpcli_session": session,
            "dpcli_snapshot": result,
            "dpcli_snapshot_ref": snapshot_ref,
            "dpcli_agent_view": agent_view,
            "dpcli_snapshot_index": index_summary,
            "dpcli_observer_diagnostics": diagnostics,
            "dom_skeleton": text,
            "dom_hash": hashlib.md5(text.encode()).hexdigest(),
            "current_url": str(page.get("url") or state.get("current_url", "")),
        },
        goto="Planner",
    )


def _collect_all_nodes(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = result.get("data") or {}
    idx = data.get("index") or {}
    return (
        list(idx.get("interactable_elements") or [])
        + list(idx.get("surface_index") or [])
        + list(idx.get("deep_index") or [])
    )


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
    return bool(DPCLI_ENABLED and state.get("execution_mode") != "python_code")


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
            f"  action_url: {action_payload.get('url', '')}"
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

    if skill == "click" and state:
        snapshot = state.get("dpcli_snapshot") or {}
        index = snapshot.get("data", {}).get("index") if isinstance(snapshot, dict) else None
        if index and params.get("locator"):
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


def _dpcli_planner_context(state: AgentState) -> str:
    from prompts.dpcli_planner_prompts import DPCLI_PLANNER_PROMPT

    agent_view = state.get("dpcli_agent_view")
    if not agent_view:
        return ""

    try:
        import json
        view_text = json.dumps(agent_view, ensure_ascii=False, indent=2)
    except Exception:
        view_text = str(agent_view)

    return DPCLI_PLANNER_PROMPT.format(
        agent_view=view_text,
        user_task=state.get("user_task", ""),
        current_url=state.get("current_url", ""),
        finished_steps=str(state.get("finished_steps", [])),
        reflections=str(state.get("reflections", [])),
        loop_count=str(state.get("loop_count", 0)),
        execution_mode=state.get("execution_mode", "python_code"),
    )
