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
    text = str(goal or "").lower()
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


def detail_candidate_items(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [item for item in extract_items_from_result(result) if _item_url(item)]


def should_run_detail_batch(state: Dict[str, Any]) -> bool:
    if state.get("dpcli_detail_batch_ran"):
        return False
    if not goal_requests_detail_batch(state.get("user_task", "")):
        return False
    return bool(detail_candidate_items(state.get("dpcli_result") or {}))


def _safe_domain(url: str) -> str:
    try:
        domain = urlparse(url).netloc or "site"
    except Exception:
        domain = "site"
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", domain) or "site"


def build_detail_batch_action(state: Dict[str, Any], max_items: int = 100) -> Dict[str, Any]:
    result = state.get("dpcli_result") or {}
    items = detail_candidate_items(result)
    if max_items > 0:
        items = items[:max_items]
    source_url = state.get("current_url") or ((result.get("data") or {}).get("page") or {}).get("url")
    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    domain = _safe_domain(str(source_url or "site"))
    stamp = int(time.time())
    output_file = output_dir / f"dpcli_detail_{domain}_{stamp}.json"
    progress_file = output_dir / f"dpcli_detail_{domain}_{stamp}.jsonl"
    return {
        "skill": "batch-detail-extract",
        "params": {
            "items": items,
            "source_url": source_url,
            "limit": len(items),
            "extractor": "auto",
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
