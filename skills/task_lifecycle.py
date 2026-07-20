"""Deep Task Lifecycle module for composite deterministic crawl tasks."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Dict, Mapping, Optional

from skills.dpcli_task_contract import (
    _filter_input_ref,
    _plan,
    build_contract_plan,
    build_task_contract,
    evaluate_contract_items,
    merge_contract_progress,
    result_items,
)


LIFECYCLE_VERSION = 3


def _contains_text(item: Mapping[str, Any], needle: str) -> bool:
    haystack = json.dumps(item, ensure_ascii=False, default=str).lower()
    return str(needle or "").strip().lower() in haystack


def _extract_until_text(task: str) -> str:
    text = str(task or "")
    patterns = (
        r"(?:直到|直至)(?:遇到|出现|包含|看到)?[^“\"']{0,12}[“\"']([^”\"']+)[”\"']",
        r"until(?:\s+(?:finding|seeing|containing))?\s+[\"']([^\"']+)[\"']",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _extract_filters(task: str, fallback: Any) -> list[dict[str, Any]]:
    text = str(task or "")
    filters: list[dict[str, Any]] = []
    pattern = re.compile(
        r"(?:在|再在|然后在)?"
        r"(?P<hint>[\u4e00-\u9fffA-Za-z0-9 _-]{1,24}?(?:框|输入框|search box|filter box))"
        r"(?:中|里)?\s*(?:输入|填写|键入|搜索|筛选|filter|type)"
        r"[^“\"']{0,8}[“\"'](?P<value>[^”\"']+)[”\"']",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        value = match.group("value").strip()
        hint = match.group("hint").strip()
        if value:
            filters.append(
                {
                    "kind": "text",
                    "value": value,
                    "field_hint": hint,
                    "submit": True,
                }
            )
    if not filters and isinstance(fallback, dict):
        filters.append(dict(fallback))
    unique = []
    seen = set()
    for item in filters:
        identity = (
            str(item.get("field_hint") or "").lower(),
            str(item.get("value") or "").lower(),
        )
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(item)
    return unique


def _output_spec(task: str) -> dict[str, Any]:
    text = str(task or "")
    output_format = ""
    if re.search(r"\bcsv\b|保存为\s*csv", text, flags=re.IGNORECASE):
        output_format = "csv"
    elif re.search(r"\bjsonl\b|保存为\s*jsonl", text, flags=re.IGNORECASE):
        output_format = "jsonl"
    elif re.search(r"\bjson\b|保存为\s*json", text, flags=re.IGNORECASE):
        output_format = "json"
    return {
        "format": output_format,
        "required": bool(output_format),
    }


class TaskLifecycle:
    """Own compilation, decision, verification progress, and serialization."""

    version = LIFECYCLE_VERSION

    def compile(self, task: str) -> Dict[str, Any]:
        return self.normalize_contract(build_task_contract(task), task=task)

    def normalize_contract(
        self,
        contract: Mapping[str, Any],
        *,
        task: str | None = None,
    ) -> Dict[str, Any]:
        normalized = deepcopy(dict(contract))
        text = str(task if task is not None else normalized.get("task") or "")
        filters = _extract_filters(text, normalized.get("filter"))
        output = _output_spec(text)
        until_text = _extract_until_text(text)
        phases = ["navigate"]
        phases.extend(f"filter:{index}" for index in range(len(filters)))
        phases.append(f"collect:{normalized.get('collection_mode') or 'single_page'}")
        if normalized.get("detail_required"):
            phases.append("details")
        if output["required"]:
            phases.append("export")
        phases.append("complete")
        normalized.update(
            {
                "version": self.version,
                "filters": filters,
                "filter": filters[0] if filters else None,
                "phases": phases,
                "stop_conditions": {
                    "min_items": int(normalized.get("min_items") or 1),
                    "max_items": int(normalized.get("max_items") or 1),
                    "target_pages": int(normalized.get("target_pages") or 1),
                    "max_scroll_rounds": int(
                        normalized.get("max_scroll_rounds") or 0
                    ),
                    "max_stagnant_rounds": int(
                        normalized.get("max_stagnant_rounds") or 2
                    ),
                    "until_text": until_text,
                    "stop_when_exhausted": True,
                },
                "dedupe_by": "url",
                "output": output,
            }
        )
        return normalized

    def decide(
        self,
        state: Mapping[str, Any],
        contract: Optional[Mapping[str, Any]] = None,
    ) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        normalized = self.normalize_contract(
            contract or build_task_contract(str(state.get("user_task") or ""))
        )
        progress = deepcopy(dict(state.get("dpcli_task_progress") or {}))
        applied = {
            int(index)
            for index in progress.get("applied_filter_indices") or []
            if str(index).isdigit()
        }
        filters = list(normalized.get("filters") or [])
        capability_map = (state.get("dpcli_agent_view") or {}).get(
            "capability_map"
        ) or {}
        for index, filter_spec in enumerate(filters):
            if index in applied:
                continue
            input_ref = _filter_input_ref(capability_map, filter_spec)
            updates = {
                "dpcli_task_contract": normalized,
                "dpcli_task_progress": {
                    **progress,
                    "applied_filter_indices": sorted(applied),
                    "filter_applied": False,
                    "active_phase": f"filter:{index}",
                },
            }
            if input_ref:
                return (
                    _plan(
                        "type",
                        {
                            "ref": input_ref,
                            "text": str(filter_spec.get("value") or ""),
                            "submit": bool(filter_spec.get("submit", True)),
                            "filter_stage": "applied",
                            "filter_index": index,
                        },
                        (
                            "deterministic task lifecycle: "
                            f"apply filter {index + 1}/{len(filters)}"
                        ),
                    ),
                    updates,
                )
            return None, updates

        state_for_base = dict(state)
        state_for_base["dpcli_task_progress"] = {
            **progress,
            "applied_filter_indices": sorted(applied),
            "filter_applied": True,
            "active_phase": (
                "details"
                if progress.get("list_complete") and normalized.get("detail_required")
                else f"collect:{normalized.get('collection_mode') or 'single_page'}"
            ),
        }
        contract_for_base = dict(normalized)
        contract_for_base["filter"] = None
        plan, updates = build_contract_plan(state_for_base, contract_for_base)
        updates["dpcli_task_contract"] = normalized
        updated_progress = dict(updates.get("dpcli_task_progress") or {})
        updated_progress["applied_filter_indices"] = sorted(applied)
        updated_progress["filter_applied"] = True
        updates["dpcli_task_progress"] = updated_progress
        return plan, updates

    def verify_action(self, state: Mapping[str, Any], skill: str):
        raw_contract = state.get("dpcli_task_contract") or {}
        if not raw_contract or skill not in {
            "extract",
            "list-items",
            "batch-detail-extract",
        }:
            return None
        contract = self.normalize_contract(raw_contract)
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
            expected_count=1,
        )
        item_count = int(evaluation["item_count"])
        requested_count = int(expected_count)
        is_success = bool(evaluation["is_success"])
        is_partial = is_success and item_count < requested_count
        if is_partial:
            summary = (
                "partial task-contract data "
                f"({item_count}/{requested_count} items; cumulative progress)"
            )
        elif is_success:
            summary = f"task-contract data valid ({item_count} items)"
        else:
            summary = evaluation["summary"]
        return {
            "is_success": is_success,
            "is_done": False,
            "summary": summary,
            "item_count": item_count,
            "requested_count": requested_count,
            "field_coverage": evaluation["field_coverage"],
            "fix_hint": (
                "continue with task-contract progress"
                if is_success
                else "select another concrete data region matching the original task schema"
            ),
        }

    def merge_verified_result(
        self,
        state: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], bool] | None:
        raw_contract = state.get("dpcli_task_contract") or {}
        if not raw_contract:
            return None
        contract = self.normalize_contract(raw_contract)
        progress = dict(state.get("dpcli_task_progress") or {})
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
        until_text = str(
            (contract.get("stop_conditions") or {}).get("until_text") or ""
        )
        condition_met = bool(
            until_text
            and any(
                _contains_text(item, until_text)
                for item in merged.get("items") or []
                if isinstance(item, Mapping)
            )
        )
        if condition_met:
            merged["stop_condition_met"] = True
            merged["stop_reason"] = f"until_text:{until_text}"
            evaluation = {
                **evaluation,
                "is_success": True,
                "summary": f"conditional stop matched: {until_text}",
            }
            pages_done = True
        list_complete = bool(evaluation["is_success"] and pages_done)
        if is_list_action:
            merged["list_complete"] = list_complete
            merged["active_phase"] = "details" if contract.get("detail_required") else "complete"
            if contract.get("collection_mode") == "infinite_scroll":
                current_item_count = len(merged.get("items") or [])
                merged["stagnant_rounds"] = (
                    int(progress.get("stagnant_rounds") or 0) + 1
                    if previous_item_count > 0
                    and current_item_count <= previous_item_count
                    else 0
                )
        if skill == "batch-detail-extract":
            merged["detail_complete"] = bool(
                evaluation["is_success"] and pages_done
            )
            merged["active_phase"] = "complete"
        is_done = bool(
            evaluation["is_success"]
            and pages_done
            and (
                condition_met
                or not contract.get("detail_required")
                or skill == "batch-detail-extract"
            )
        )
        return merged, evaluation, is_done

    def advance_verified_page(self, state: Mapping[str, Any]) -> dict[str, Any]:
        progress = dict(state.get("dpcli_task_progress") or {})
        plan = state.get("dpcli_structured_plan") or {}
        payload = plan.get("action_payload") or {}
        intent = str(plan.get("step_intent") or "").lower()
        if intent == "click" and payload.get("page_number") is not None:
            progress["active_page"] = max(1, int(payload["page_number"]))
            progress["failed_region_refs"] = []
        elif intent == "type" and payload.get("filter_stage") == "applied":
            index = payload.get("filter_index")
            if index is None:
                progress["filter_applied"] = True
            else:
                applied = {
                    int(value)
                    for value in progress.get("applied_filter_indices") or []
                    if str(value).isdigit()
                }
                applied.add(int(index))
                progress["applied_filter_indices"] = sorted(applied)
                contract = self.normalize_contract(
                    state.get("dpcli_task_contract") or {}
                )
                progress["filter_applied"] = len(applied) >= len(
                    contract.get("filters") or []
                )
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

    @staticmethod
    def mark_failed_region(
        state: Mapping[str, Any],
        progress_override: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        progress = dict(
            progress_override
            if progress_override is not None
            else state.get("dpcli_task_progress") or {}
        )
        failed_refs = list(progress.get("failed_region_refs") or [])
        plan = state.get("dpcli_structured_plan") or {}
        payload = plan.get("action_payload") or {}
        ref = (
            payload.get("target_ref")
            or payload.get("group_ref")
            or payload.get("ref")
        )
        if ref and ref not in failed_refs:
            failed_refs.append(str(ref))
        progress["failed_region_refs"] = failed_refs
        return progress

    def checkpoint(self, state: Mapping[str, Any]) -> dict[str, Any]:
        contract = self.normalize_contract(
            state.get("dpcli_task_contract")
            or build_task_contract(str(state.get("user_task") or ""))
        )
        return {
            "lifecycle_version": self.version,
            "task_contract": contract,
            "task_progress": deepcopy(dict(state.get("dpcli_task_progress") or {})),
        }

    def restore(self, checkpoint: Mapping[str, Any]) -> dict[str, Any]:
        version = int(checkpoint.get("lifecycle_version") or 0)
        if version > self.version:
            raise ValueError(
                f"unsupported lifecycle version {version}; current={self.version}"
            )
        contract = self.normalize_contract(
            checkpoint.get("task_contract") or {}
        )
        return {
            "dpcli_task_contract": contract,
            "dpcli_task_progress": deepcopy(
                dict(checkpoint.get("task_progress") or {})
            ),
        }


task_lifecycle = TaskLifecycle()
