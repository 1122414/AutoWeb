from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


from langgraph.types import Command

from core.state_v2 import AgentState
from core.nodes._verification import _normalize_failure_scope, _build_verification_result
from skills.logger import logger

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
        DPCLI_HEADLESS,
        DPCLI_OBSERVER_ENABLED,
        DPCLI_OBSERVER_FALLBACK_TO_DOM,
        DPCLI_SESSION,
    )
    should_use_dpcli_observer = (
        DPCLI_OBSERVER_ENABLED
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
    return bool(DPCLI_ENABLED and state.get("execution_mode") == "dp_cli")


def _dpcli_action_context(state: AgentState) -> str:
    url = state.get("current_url", "")
    task = state.get("user_task", "")
    plan = state.get("plan", "")
    suggestions = state.get("locator_suggestions", [])

    locators_text = ""
    if suggestions:
        parts = []
        for entry in suggestions:
            strategies = entry.get("strategies", [])
            if isinstance(strategies, list):
                for s in strategies:
                    if isinstance(s, dict):
                        loc = s.get("locator", "")
                        reason = s.get("reason", "")
                        if loc:
                            parts.append(f"- {loc} ({reason})" if reason else f"- {loc}")
            elif isinstance(strategies, dict):
                loc = strategies.get("locator", "")
                if loc:
                    parts.append(f"- {loc}")
        locators_text = "\n".join(parts)

    return (
        f"【当前页面】\nURL: {url}\n\n"
        f"【用户任务】\n{task}\n\n"
        f"【当前计划】\n{plan}\n\n"
        f"【定位策略】\n{locators_text or '(无)'}"
    )


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
        return "缺少 skill 字段"
    if not isinstance(params, dict):
        return "params 必须是对象"

    required = {
        "click": ["ref", "locator"],
        "type": ["ref", "locator"],
        "find": ["ref", "locator"],
        "expand": ["ref", "locator"],
        "list-items": ["ref", "locator"],
    }

    if skill in required:
        has_any = any(bool(params.get(k)) for k in required[skill])
        if not has_any:
            return f"{skill} 需要 {required[skill]} 中的一个"

    if skill == "click" and state:
        snapshot = state.get("dpcli_snapshot") or {}
        index = snapshot.get("data", {}).get("index") if isinstance(snapshot, dict) else None
        if index and params.get("locator"):
            return "click 在有 snapshot 时必须使用 ref 而非 locator"

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