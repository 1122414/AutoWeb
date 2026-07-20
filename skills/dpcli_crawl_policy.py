from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from config import OUTPUT_DIR


DETAIL_GOAL_TOKENS = (
    "详情",
    "详细",
    "简介",
    "介绍",
    "点进去",
    "进入每",
    "每一",
    "detail",
    "details",
    "profile",
)


def goal_requests_detail_batch(goal: str) -> bool:
    from skills.dpcli_task_contract import build_task_contract

    text = str(goal or "").lower()
    if build_task_contract(text).get("detail_required"):
        return True
    negative_tokens = (
        "不要进入详情",
        "不进入详情",
        "无需进入详情",
        "只提取详情链接",
        "仅提取详情链接",
        "do not open detail",
        "without opening detail",
    )
    if any(token in text for token in negative_tokens):
        return False
    link_only = any(
        token in text
        for token in ("详情链接", "详情 url", "detail link", "detail url")
    )
    detail_content = any(
        token in text
        for token in (
            "进入详情",
            "打开详情",
            "点进去",
            "简介",
            "描述",
            "detail page",
            "description",
            "summary",
        )
    )
    if link_only and not detail_content:
        return False
    return any(token in text for token in DETAIL_GOAL_TOKENS)


def extract_items_from_result(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(result, dict) or not result.get("ok"):
        return []
    if result.get("action") != "extract":
        return []
    data = result.get("data") or {}
    if not isinstance(data, dict):
        return []
    items = data.get("items") or []
    return [item for item in items if isinstance(item, dict)]


def _item_url(item: Dict[str, Any]) -> str:
    value = item.get("detail_url") or item.get("url") or item.get("href") or ""
    return str(value or "").strip()


def _is_valid_detail_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def detail_candidate_items(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    return _detail_candidates(extract_items_from_result(result))


def _detail_candidates(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    seen_urls = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        url = _item_url(item)
        if not _is_valid_detail_url(url):
            continue
        normalized = url.rstrip("/")
        if normalized in seen_urls:
            continue
        seen_urls.add(normalized)
        candidates.append(item)
    return candidates


def should_run_detail_batch(state: Dict[str, Any]) -> bool:
    if state.get("dpcli_detail_batch_ran"):
        return False
    contract = state.get("dpcli_task_contract")
    if isinstance(contract, dict) and not contract.get("detail_required"):
        return False
    progress = state.get("dpcli_task_progress") or {}
    if isinstance(contract, dict) and not progress.get("list_complete"):
        return False
    if not goal_requests_detail_batch(state.get("user_task", "")):
        return False
    items = (
        progress.get("items")
        if isinstance(progress, dict)
        else None
    ) or detail_candidate_items(state.get("dpcli_result") or {})
    return bool(_detail_candidates(items))


def _safe_domain(url: str) -> str:
    try:
        domain = urlparse(url).netloc or "site"
    except Exception:
        domain = "site"
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", domain) or "site"


def build_detail_batch_action(state: Dict[str, Any], max_items: int = 100) -> Dict[str, Any]:
    result = state.get("dpcli_result") or {}
    progress = state.get("dpcli_task_progress") or {}
    progress_items = progress.get("items") if isinstance(progress, dict) else None
    items = _detail_candidates(progress_items or extract_items_from_result(result))
    if max_items > 0:
        items = items[:max_items]
    source_url = state.get("current_url") or ((result.get("data") or {}).get("page") or {}).get("url")
    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    domain = _safe_domain(str(source_url or "site"))
    stamp = int(time.time())
    contract = state.get("dpcli_task_contract") or {}
    detail_schema = list(contract.get("detail_schema") or [])
    output_file = output_dir / f"dpcli_detail_{domain}_{stamp}.json"
    progress_file = output_dir / f"dpcli_detail_{domain}_{stamp}.jsonl"
    return {
        "skill": "batch-detail-extract",
        "params": {
            "items": items,
            "source_url": source_url,
            "target_pages": int(contract.get("target_pages") or 1),
            "list_pages_extracted": len(progress.get("completed_pages") or []),
            "limit": len(items),
            "schema": detail_schema,
            "extractor": "legacy-js",
            "navigation_mode": "direct",
            "fallback_mode": "direct",
            "wait_time": 0.5,
            "max_retries": 1,
            "item_timeout": 120,
            "ai_timeout": 45,
            "output_file": str(output_file.resolve()),
            "progress_file": str(progress_file.resolve()),
        },
        "reason": "用户目标要求详情信息，使用 dp_cli 批量详情提取",
    }
