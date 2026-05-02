"""Verification 结果构造、解析、失败判断工具。"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _normalize_failure_scope(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "global" if text == "global" else "local"


def _normalize_verification_source(value: Any, default: str = "verifier") -> str:
    valid = {"verifier", "executor", "error_handler", "manual"}
    text = str(value or default).strip().lower()
    return text if text in valid else default


def _build_verification_result(
    *,
    is_success: bool,
    summary: str,
    source: str,
    is_done: bool = False,
    failure_scope: str = "local",
    failed_action: str = "",
    failed_locator: str = "",
    evidence: str = "",
    fix_hint: str = "",
) -> Dict[str, Any]:
    success = bool(is_success)
    return {
        "is_success": success,
        "is_done": bool(is_done) if success else False,
        "summary": str(summary or "Step executed.").strip(),
        "source": _normalize_verification_source(source),
        "failure_scope": _normalize_failure_scope(failure_scope),
        "failed_action": str(failed_action or "").strip(),
        "failed_locator": str(failed_locator or "").strip(),
        "evidence": str(evidence or "").strip(),
        "fix_hint": str(fix_hint or "").strip(),
    }


def _coerce_verification_result(
    verification: Optional[Dict[str, Any]],
    *,
    fallback_is_success: bool = False,
    fallback_summary: str = "Step executed.",
    fallback_source: str = "verifier",
    fallback_is_done: bool = False,
    fallback_failure_scope: str = "local",
    fallback_failed_action: str = "",
    fallback_failed_locator: str = "",
    fallback_evidence: str = "",
    fallback_fix_hint: str = "",
) -> Dict[str, Any]:
    payload = verification or {}
    return _build_verification_result(
        is_success=bool(payload.get("is_success", fallback_is_success)),
        is_done=bool(payload.get("is_done", fallback_is_done)),
        summary=str(payload.get("summary", fallback_summary)),
        source=str(payload.get("source", fallback_source)),
        failure_scope=str(payload.get(
            "failure_scope", fallback_failure_scope)),
        failed_action=str(payload.get(
            "failed_action", fallback_failed_action)),
        failed_locator=str(payload.get(
            "failed_locator", fallback_failed_locator)),
        evidence=str(payload.get("evidence", fallback_evidence)),
        fix_hint=str(payload.get("fix_hint", fallback_fix_hint)),
    )


def _is_failed_verification(verification: Optional[Dict[str, Any]]) -> bool:
    return bool(verification) and bool(verification.get("is_success", True)) is False


def _parse_verifier_result_content(content: str) -> Dict[str, Any]:
    summary = "Step executed."
    failure_scope = "local"
    failed_action = ""
    failed_locator = ""
    evidence = ""
    fix_hint = ""
    is_success = "Status: STEP_SUCCESS" in content

    for raw_line in (content or "").split("\n"):
        line = raw_line.strip()
        line_lower = line.lower()
        if line.startswith("Summary:"):
            summary = line.replace("Summary:", "", 1).strip() or summary
        elif line_lower.startswith("failurescope:"):
            failure_scope = line.split(
                ":", 1)[1].strip() if ":" in line else failure_scope
        elif line_lower.startswith("failedaction:"):
            failed_action = line.split(
                ":", 1)[1].strip() if ":" in line else failed_action
        elif line_lower.startswith("failedlocator:"):
            failed_locator = line.split(
                ":", 1)[1].strip() if ":" in line else failed_locator
        elif line_lower.startswith("evidence:"):
            evidence = line.split(
                ":", 1)[1].strip() if ":" in line else evidence
        elif line_lower.startswith("fixhint:"):
            fix_hint = line.split(
                ":", 1)[1].strip() if ":" in line else fix_hint

    return {
        "is_success": is_success,
        "summary": summary,
        "failure_scope": _normalize_failure_scope(failure_scope),
        "failed_action": failed_action,
        "failed_locator": failed_locator,
        "evidence": evidence,
        "fix_hint": fix_hint,
    }


def _verification_focus_text(verification: Optional[Dict[str, Any]]) -> str:
    if not _is_failed_verification(verification):
        return "(无)"
    v = verification or {}
    scope = _normalize_failure_scope(v.get("failure_scope", "local"))
    action = str(v.get("failed_action", "")).strip() or "(未提供)"
    locator = str(v.get("failed_locator", "")).strip() or "(未提供)"
    evidence = str(v.get("evidence", "")).strip() or str(
        v.get("summary", "")).strip() or "(未提供)"
    fix_hint = str(v.get("fix_hint", "")).strip() or "(未提供)"
    return (
        f"- failure_scope: {scope}\n"
        f"- failed_action: {action}\n"
        f"- failed_locator: {locator}\n"
        f"- evidence: {evidence}\n"
        f"- fix_hint: {fix_hint}"
    )
