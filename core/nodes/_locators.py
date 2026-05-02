"""Locator 提取、归一化、校验、Dry-Run 探测工具。"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from core.nodes._utils import _get_tab
from skills.logger import logger


def _extract_locator_info(state: dict) -> str:
    suggestions = state.get("locator_suggestions", [])
    if not suggestions:
        return ""
    parts = []
    for entry in suggestions:
        strategies = entry.get("strategies", [])
        if isinstance(strategies, list):
            for s in strategies:
                if isinstance(s, dict):
                    loc = s.get("locator", "")
                    reason = s.get("reason", "")
                    if loc:
                        parts.append(f"{loc} ({reason})" if reason else loc)
        elif isinstance(strategies, dict):
            loc = strategies.get("locator", "")
            if loc:
                parts.append(loc)
    return " | ".join(parts) if parts else ""


def _extract_domain_key_from_url(url: str) -> str:
    try:
        parsed = urlparse(url or "")
        host = (parsed.hostname or "").strip().lower()
        if not host:
            return ""
        try:
            import tldextract

            extractor = tldextract.TLDExtract(suffix_list_urls=None)
            ext = extractor(host)
            if ext.domain and ext.suffix:
                return f"{ext.domain}.{ext.suffix}"[:255]
        except Exception:
            pass
        parts = [x for x in host.split(".") if x]
        if len(parts) >= 2:
            return ".".join(parts[-2:])[:255]
        return host[:255]
    except Exception:
        return ""


def _build_step_context(finished_steps: list) -> str:
    from config import DOM_CACHE_STEP_WINDOW, DOM_CACHE_STEP_TEXT_MAX

    steps = finished_steps or []
    window = max(1, int(DOM_CACHE_STEP_WINDOW))
    last_steps = steps[-window:] if steps else []
    text = " | ".join([str(x).strip() for x in last_steps if str(x).strip()])
    return text[:max(100, int(DOM_CACHE_STEP_TEXT_MAX))]


def _extract_locator_candidates(locator_info: str, code: str) -> list:
    candidates = []

    info = str(locator_info or "").strip()
    if info:
        for part in info.split("|"):
            item = part.strip()
            if not item:
                continue
            loc = item.split("(", 1)[0].strip()
            if loc:
                candidates.append(loc)

    code_text = code or ""
    pattern = re.compile(r"(?:tab|new_tab|page)\.ele\(\s*(['\"])(.+?)\1")
    for _, locator in pattern.findall(code_text):
        loc = (locator or "").strip()
        if loc:
            candidates.append(loc)

    seen = set()
    dedup = []
    for loc in candidates:
        if loc in seen:
            continue
        seen.add(loc)
        dedup.append(loc)
    return dedup


def _extract_locators_from_strategies(strategies: Any) -> list:
    locators = []
    if isinstance(strategies, dict):
        strategies = [strategies]
    if not isinstance(strategies, list):
        return locators

    for item in strategies:
        if not isinstance(item, dict):
            continue
        loc = str(item.get("locator", "")).strip()
        if loc:
            locators.append(loc)
        sub = item.get("sub_locators", {})
        if isinstance(sub, dict):
            for value in sub.values():
                if isinstance(value, str) and value.strip():
                    locators.append(value.strip())

    seen = set()
    dedup = []
    for loc in locators:
        if loc in seen:
            continue
        seen.add(loc)
        dedup.append(loc)
    return dedup


def _normalize_locator_token(locator: str) -> str:
    text = str(locator or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    return text


def _has_locator_overlap(failed_locator: str, candidates: list) -> bool:
    failed = _normalize_locator_token(failed_locator)
    if not failed:
        return False
    for item in candidates or []:
        token = _normalize_locator_token(item)
        if not token:
            continue
        if failed == token or failed in token or token in failed:
            return True
    return False


def _sanitize_locator(locator: str) -> str:
    loc = str(locator or "").strip()
    if not loc:
        return loc
    loc = re.sub(r'/@[\w-]+$', '', loc)
    loc = re.sub(r'/text\(\)$', '', loc)
    if loc.startswith('x:.//'):
        loc = loc.replace("x:.", "x:", 1)
    if loc.startswith('.//'):
        loc = loc.replace(".", "x:", 1)
    loc = re.sub(r'@@(?:tag|attr)=\S+', '', loc)
    return loc.strip()


def _is_valid_element(ele) -> bool:
    if ele is None:
        return False
    try:
        class_name = getattr(ele.__class__, "__name__", "").strip().lower()
    except Exception:
        class_name = ""
    if class_name == "noneelement":
        return False
    try:
        if "noneelement" in repr(ele).lower():
            return False
    except Exception:
        pass
    return True


def _probe_locator(search_root, locator: str, timeout: float) -> dict:
    if search_root is None:
        return {"ok": False, "element": None, "reason": "search_root为空"}
    loc = _sanitize_locator(str(locator or "").strip())
    if not loc:
        return {"ok": False, "element": None, "reason": "locator为空"}
    try:
        ele = search_root.ele(loc, timeout=timeout)
        if _is_valid_element(ele):
            return {"ok": True, "element": ele, "reason": ""}
        return {"ok": False, "element": None, "reason": "not-found"}
    except Exception as e:
        return {"ok": False, "element": None, "reason": f"Exception:{type(e).__name__}"}


def _dry_run_observer_strategies(
    config, strategies, timeout_seconds, **_ignored
) -> tuple[bool, list[str], int]:
    tab = _get_tab(config)
    if tab is None:
        return False, ["无可用浏览器标签页"], 0

    strategy_list = _normalize_strategy_list(strategies)
    if not strategy_list:
        return False, ["无可校验locator"], 0

    failed_locators = []
    validated_count = 0
    for idx, item in enumerate(strategy_list, 1):
        main_loc = str(item.get("locator", "")).strip()
        if not main_loc:
            failed_locators.append(f"[{idx}] main:(空)")
            continue

        validated_count += 1
        main_result = _probe_locator(tab, main_loc, timeout_seconds)
        if not main_result["ok"]:
            failed_locators.append(
                f"[{idx}] main:{main_loc} | {main_result['reason']}")

        for key, val in (item.get("sub_locators") or {}).items():
            sub_loc = str(val or "").strip()
            if not sub_loc:
                continue
            validated_count += 1
            sub_result = _probe_locator(tab, sub_loc, timeout_seconds)
            if not sub_result["ok"]:
                failed_locators.append(
                    f"[{idx}] sub[{key}]:{sub_loc} | {sub_result['reason']}")

    if not validated_count and not failed_locators:
        failed_locators.append("无可校验locator")
    return len(failed_locators) == 0, failed_locators, validated_count


def _dry_run_cache_hit_locators(
    config, locator_candidates, timeout_seconds, **_ignored
) -> tuple[bool, str]:
    tab = _get_tab(config)
    if tab is None:
        return False, "无可用浏览器标签页"
    if not locator_candidates:
        return False, "无可校验locator"

    for loc in locator_candidates:
        locator = str(loc or "").strip()
        if not locator:
            return False, "空locator"
        result = _probe_locator(tab, locator, timeout_seconds)
        if not result["ok"]:
            logger.info(
                f"   ❌ [DryRunProbe] locator={locator}, reason={result['reason']}")
            return False, locator
    return True, ""


def _normalize_strategy_list(strategies: Any) -> list:
    if isinstance(strategies, dict):
        return [strategies]
    if isinstance(strategies, list):
        return [item for item in strategies if isinstance(item, dict)]
    return []
