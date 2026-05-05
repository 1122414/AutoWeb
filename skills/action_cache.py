from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from config import ACTION_CACHE_STORE_PATH
from skills.logger import logger, trace_log


@dataclass
class ActionCacheHit:
    id: str
    score: float
    action: Dict[str, Any]
    goal: str
    user_task: str
    url_pattern: str
    created_at: str


def _domain(url: str) -> str:
    try:
        return urlparse(url or "").netloc.lower()
    except Exception:
        return ""


def _tokens(text: str) -> set[str]:
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in str(text or ""))
    return {item for item in normalized.split() if len(item) >= 2}


class ActionCacheManager:
    def __init__(self, store_path: str = ACTION_CACHE_STORE_PATH) -> None:
        self.store_path = Path(store_path)

    def search(
        self,
        *,
        user_task: str,
        goal: str,
        url: str,
        snapshot_view: Optional[Dict[str, Any]] = None,
        top_k: int = 5,
    ) -> List[ActionCacheHit]:
        trace_log(f"ActionCache search: url={url[:60]}, top_k={top_k}")
        query_tokens = _tokens(f"{user_task} {goal} {json.dumps(snapshot_view or {}, ensure_ascii=False)}")
        query_domain = _domain(url)
        hits: List[ActionCacheHit] = []
        records = self._load()
        logger.debug(f"   🔍 [ActionCache] 检索中: domain={query_domain}, records={len(records)}")
        for record in self._load():
            if query_domain and record.get("domain_key") and record.get("domain_key") != query_domain:
                continue
            record_tokens = _tokens(
                f"{record.get('user_task', '')} {record.get('goal', '')} {record.get('snapshot_signature', '')}"
            )
            if not query_tokens or not record_tokens:
                score = 0.0
            else:
                score = len(query_tokens & record_tokens) / max(len(query_tokens | record_tokens), 1)
            if score <= 0:
                continue
            hits.append(ActionCacheHit(
                id=str(record.get("cache_id") or ""),
                score=score,
                action=record.get("action_json") or {},
                goal=str(record.get("goal") or ""),
                user_task=str(record.get("user_task") or ""),
                url_pattern=str(record.get("url_pattern") or ""),
                created_at=str(record.get("created_at") or ""),
            ))
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:top_k]

    def save(
        self,
        *,
        user_task: str,
        goal: str,
        url: str,
        action: Dict[str, Any],
        snapshot_view: Optional[Dict[str, Any]] = None,
        result_summary: str = "",
    ) -> str:
        trace_log(f"ActionCache save: url={url[:60]}, task_type={(action or {}).get('skill', '')}")
        records = self._load()
        cache_id = f"action_{uuid.uuid4().hex[:12]}"
        records.append({
            "cache_id": cache_id,
            "user_task": user_task,
            "goal": goal,
            "url_pattern": url,
            "domain_key": _domain(url),
            "snapshot_signature": self._snapshot_signature(snapshot_view),
            "task_type": (action or {}).get("skill", ""),
            "action_json": action,
            "result_summary": result_summary,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "success_count": 1,
            "failure_count": 0,
        })
        self._write(records)
        logger.info(f"   ✅ [ActionCache] 已保存: id={cache_id}, task_type={(action or {}).get('skill', '')}")
        return cache_id

    def record_failure(self, cache_id: str, reason: str = "") -> None:
        trace_log(f"ActionCache record_failure: id={cache_id}, reason={reason}")
        records = self._load()
        changed = False
        for record in records:
            if record.get("cache_id") == cache_id:
                record["failure_count"] = int(record.get("failure_count") or 0) + 1
                record["last_failure_reason"] = reason
                changed = True
                break
        if changed:
            self._write(records)

    def _load(self) -> List[Dict[str, Any]]:
        if not self.store_path.exists():
            return []
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def _write(self, records: List[Dict[str, Any]]) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug(f"   💾 [ActionCache] 写入 {len(records)} 条记录到 {self.store_path}")

    def _snapshot_signature(self, snapshot_view: Optional[Dict[str, Any]]) -> str:
        if not snapshot_view:
            return ""
        compact = {
            "interactable": snapshot_view.get("interactable_elements", [])[:10],
            "data_regions": snapshot_view.get("data_regions", [])[:5],
        }
        return json.dumps(compact, ensure_ascii=False, sort_keys=True)


action_cache_manager = ActionCacheManager()
