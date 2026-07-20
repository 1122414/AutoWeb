from __future__ import annotations

import copy
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
        query_signature = self._snapshot_signature(snapshot_view)
        query_tokens = _tokens(f"{user_task} {goal} {query_signature}")
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
            rebound_action = self._rebind_action(
                record.get("action_json") or {},
                record.get("action_target"),
                snapshot_view,
            )
            if rebound_action is None:
                continue
            hits.append(ActionCacheHit(
                id=str(record.get("cache_id") or ""),
                score=score,
                action=rebound_action,
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
            "action_target": self._action_target_descriptor(
                snapshot_view,
                action,
            ),
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
        capability_map = snapshot_view.get("capability_map")
        if isinstance(capability_map, dict):
            compact_capabilities = {}
            for capability_name in sorted(capability_map):
                entries = capability_map.get(capability_name)
                if not isinstance(entries, list):
                    continue
                compact_entries = [
                    self._compact_capability_item(capability_name, entry)
                    for entry in entries[:5]
                    if isinstance(entry, dict)
                ]
                compact_entries = [entry for entry in compact_entries if entry]
                if compact_entries:
                    compact_capabilities[capability_name] = compact_entries
            return json.dumps(
                {"capability_map": compact_capabilities},
                ensure_ascii=False,
                sort_keys=True,
            )
        compact = {
            "interactable": snapshot_view.get("interactable_elements", [])[:10],
            "data_regions": snapshot_view.get("data_regions", [])[:5],
        }
        return json.dumps(compact, ensure_ascii=False, sort_keys=True)

    def _action_target_descriptor(
        self,
        snapshot_view: Optional[Dict[str, Any]],
        action: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        _, target_ref = self._action_target_param(action)
        if not target_ref:
            return None
        for ref, descriptor in self._snapshot_targets(snapshot_view):
            if ref == target_ref:
                return descriptor
        return None

    def _rebind_action(
        self,
        action: Dict[str, Any],
        stored_target: Any,
        snapshot_view: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        param_name, target_ref = self._action_target_param(action)
        rebound = copy.deepcopy(action)
        if not param_name or not target_ref:
            return rebound

        current_targets = self._snapshot_targets(snapshot_view)
        if not current_targets:
            return None

        for current_ref, current_descriptor in current_targets:
            if current_ref != target_ref:
                continue
            if not isinstance(stored_target, dict) or self._target_matches(
                stored_target,
                current_descriptor,
            ):
                return rebound

        if not isinstance(stored_target, dict):
            return None

        candidates = [
            (current_ref, descriptor)
            for current_ref, descriptor in current_targets
            if self._target_matches(stored_target, descriptor)
        ]
        if len(candidates) == 1:
            rebound["params"][param_name] = candidates[0][0]
            return rebound

        same_position = [
            (current_ref, descriptor)
            for current_ref, descriptor in candidates
            if descriptor.get("ordinal") == stored_target.get("ordinal")
            and descriptor.get("control_ordinal")
            == stored_target.get("control_ordinal")
        ]
        if len(same_position) == 1:
            rebound["params"][param_name] = same_position[0][0]
            return rebound
        return None

    @staticmethod
    def _action_target_param(action: Dict[str, Any]) -> tuple[str, str]:
        params = action.get("params")
        if not isinstance(params, dict):
            return "", ""
        for name in ("ref", "target_ref", "group_ref"):
            value = str(params.get(name) or "").strip()
            if value:
                return name, value
        return "", ""

    def _snapshot_targets(
        self,
        snapshot_view: Optional[Dict[str, Any]],
    ) -> List[tuple[str, Dict[str, Any]]]:
        if not isinstance(snapshot_view, dict):
            return []
        targets: List[tuple[str, Dict[str, Any]]] = []
        capability_map = snapshot_view.get("capability_map")
        if isinstance(capability_map, dict):
            for capability_name, entries in capability_map.items():
                if not isinstance(entries, list):
                    continue
                for ordinal, item in enumerate(entries):
                    if not isinstance(item, dict):
                        continue
                    ref = str(item.get("ref") or "").strip()
                    descriptor = self._compact_capability_item(
                        str(capability_name),
                        item,
                    )
                    descriptor.update(
                        {
                            "capability": str(capability_name),
                            "ordinal": ordinal,
                        }
                    )
                    if ref:
                        targets.append((ref, descriptor))

                    controls = item.get("controls")
                    if not isinstance(controls, list):
                        continue
                    for control_ordinal, control in enumerate(controls):
                        if not isinstance(control, dict):
                            continue
                        control_ref = str(control.get("ref") or "").strip()
                        if not control_ref:
                            continue
                        control_descriptor = self._compact_capability_item(
                            f"{capability_name}.controls",
                            control,
                        )
                        control_descriptor.update(
                            {
                                "capability": f"{capability_name}.controls",
                                "ordinal": ordinal,
                                "control_ordinal": control_ordinal,
                            }
                        )
                        targets.append((control_ref, control_descriptor))
            return targets

        for capability_name, field_name in (
            ("interactable_elements", "interactable_elements"),
            ("data_regions", "data_regions"),
        ):
            entries = snapshot_view.get(field_name)
            if not isinstance(entries, list):
                continue
            for ordinal, item in enumerate(entries):
                if not isinstance(item, dict):
                    continue
                ref = str(item.get("ref") or "").strip()
                if not ref:
                    continue
                descriptor = self._compact_capability_item(
                    capability_name,
                    item,
                )
                descriptor.update(
                    {
                        "capability": capability_name,
                        "ordinal": ordinal,
                    }
                )
                targets.append((ref, descriptor))
        return targets

    @staticmethod
    def _target_matches(
        stored: Dict[str, Any],
        current: Dict[str, Any],
    ) -> bool:
        if stored.get("capability") != current.get("capability"):
            return False
        ignored = {"ordinal", "control_ordinal"}
        comparable = [
            key
            for key, value in stored.items()
            if key not in ignored and value not in (None, "", [])
        ]
        return bool(comparable) and all(
            current.get(key) == stored.get(key)
            for key in comparable
        )

    @staticmethod
    def _compact_capability_item(
        capability_name: str,
        item: Dict[str, Any],
    ) -> Dict[str, Any]:
        stable_fields = (
            "role",
            "kind",
            "tag",
            "input_type",
            "placeholder",
            "label",
            "direction",
            "enabled",
        )
        compact = {
            field: item[field]
            for field in stable_fields
            if item.get(field) not in (None, "", [])
        }
        if capability_name not in {"data_regions", "content_regions"}:
            name = str(item.get("name") or "").strip()
            if name:
                compact["name"] = name[:80]

        actions = item.get("available_actions")
        if isinstance(actions, list):
            compact["available_actions"] = [
                str(action)
                for action in actions[:8]
                if str(action or "").strip()
            ]

        controls = item.get("controls")
        if isinstance(controls, list):
            compact_controls = []
            for control in controls[:8]:
                if not isinstance(control, dict):
                    continue
                stable_control = {
                    field: control[field]
                    for field in (
                        "role",
                        "tag",
                        "label",
                        "direction",
                        "enabled",
                    )
                    if control.get(field) not in (None, "", [])
                }
                if stable_control:
                    compact_controls.append(stable_control)
            if compact_controls:
                compact["controls"] = compact_controls
        return compact


action_cache_manager = ActionCacheManager()
