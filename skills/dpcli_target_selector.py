"""
TargetSelector - dp_cli target ref confirmation layer (pure class).

This module contains no LangGraph/LangChain/tiktoken dependencies.
Smoke scripts and tests can import TargetSelector directly from here.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from skills.dpcli_snapshot_query import SnapshotQueryEngine
from skills.dpcli_snapshot_store import SnapshotStore
from skills.logger import logger


def _normalize_target_constraints(target_request: Dict[str, Any]) -> Dict[str, Any]:
    constraints = dict(target_request.get("constraints") or {})

    role = target_request.get("role")
    if role and "role" not in constraints:
        constraints["role"] = role if isinstance(role, list) else [role]

    text_or_name = target_request.get("text_or_name")
    if text_or_name and "text_or_name" not in constraints:
        constraints["text_or_name"] = (
            text_or_name if isinstance(text_or_name, list) else [text_or_name]
        )

    region_hint = target_request.get("region_hint")
    if region_hint and "region_hint" not in constraints:
        constraints["region_hint"] = region_hint

    near = target_request.get("near")
    if near and "near" not in constraints:
        constraints["near"] = near

    return constraints


class TargetSelector:
    """
    Target selector using deterministic snapshot queries with optional LLM arbitration.

    Output:
    {
        "status": "selected|need_approval|not_found|need_more_observation",
        "target_ref": "e12",
        "confidence": 1.0,
        "selection_mode": "deterministic|llm_arbitrated",
        "evidence": {...},
        "alternatives": [...],
        "approval_required": bool
    }
    """

    def __init__(
        self,
        store: Optional[SnapshotStore] = None,
        llm: Any = None,
    ):
        self._store = store or SnapshotStore()
        self._engine = SnapshotQueryEngine(self._store)
        self._llm = llm

    def select(
        self,
        query: Dict[str, Any],
        snapshot_ref: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        intent = query.get("intent", "click")
        target_hint = query.get("target_hint", "")
        constraints = query.get("target_constraints", {}) or {}

        if snapshot_ref:
            self._engine.load_from_ref(snapshot_ref)

        if not self._engine.is_loaded:
            return {
                "status": "need_more_observation",
                "target_ref": None,
                "target_kind": None,
                "skill_hint": intent,
                "selection_mode": "none",
                "evidence": {},
                "alternatives": [],
                "approval_required": False,
                "reason": "no snapshot loaded",
            }

        candidates = self._retrieve_candidates(intent, target_hint, constraints)
        candidate_pack = self._build_candidate_pack(candidates, intent, target_hint)

        if not candidate_pack:
            return {
                "status": "not_found",
                "target_ref": None,
                "target_kind": None,
                "skill_hint": intent,
                "selection_mode": "none",
                "evidence": {},
                "alternatives": [],
                "approval_required": False,
                "reason": f"no candidates found for '{target_hint}'",
            }

        if len(candidate_pack) == 1 and candidate_pack[0].get("confidence", 0) >= 0.8:
            return self._deterministic_result(candidate_pack[0], intent)

        conflicts = self._detect_conflicts(candidate_pack)
        if not conflicts:
            best = max(candidate_pack, key=lambda c: c.get("confidence", 0))
            if best.get("confidence", 0) >= 0.9:
                return self._deterministic_result(best, intent)

        return {
            "status": "need_approval",
            "target_ref": None,
            "target_kind": None,
            "skill_hint": intent,
            "selection_mode": "conflict_detected",
            "evidence": {},
            "alternatives": candidate_pack[:8],
            "approval_required": True,
            "approval_reason": (
                f"multiple candidates ({len(candidate_pack)}) "
                f"or conflicts ({len(conflicts)})"
            ),
            "conflicts": conflicts,
        }

    def select_from_structured_plan(
        self,
        structured_plan: Dict[str, Any],
        snapshot_ref: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.select(structured_plan, snapshot_ref)

    def verify_selection(
        self, target_ref: str, intent: str = "click"
    ) -> Dict[str, Any]:
        verification = self._engine.verify_ref(target_ref, intent)
        if not verification.get("valid"):
            return {
                "status": "not_found",
                "target_ref": target_ref,
                "target_kind": None,
                "skill_hint": intent,
                "selection_mode": "verification_failed",
                "evidence": verification,
                "alternatives": [],
                "approval_required": False,
                "reason": "; ".join(verification.get("issues", [])),
            }
        node = self._engine.get_ref(target_ref)
        return {
            "status": "selected",
            "target_ref": target_ref,
            "target_kind": node.get("ref_type", "element") if node else None,
            "skill_hint": intent,
            "selection_mode": "deterministic",
            "confidence": 1.0,
            "evidence": {
                "role": verification.get("role", ""),
                "name": verification.get("name", ""),
                "text": verification.get("text", ""),
                "source": "full_snapshot",
                "verified": True,
            },
            "alternatives": [],
            "approval_required": False,
        }

    def _retrieve_candidates(
        self, intent: str, target_hint: str, constraints: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []

        roles = constraints.get("role") or []
        text_hints = (
            constraints.get("text_or_name") or
            ([target_hint] if target_hint else [])
        )

        for text in text_hints:
            query: Dict[str, Any] = {}
            for k, v in constraints.items():
                if k not in ("role", "text_or_name", "near"):
                    query[k] = v
            if roles:
                query["role"] = roles
            query["text"] = text

            if intent in ("click", "type"):
                query["ref_type"] = "element"
            elif intent in ("expand", "list-items", "extract"):
                query["ref_type"] = ["element", "container"]

            results = self._engine.search_snapshot(query, limit=10)
            for r in results:
                if r.get("ref") not in [c.get("ref") for c in candidates]:
                    candidates.append(r)

        if not candidates and roles:
            query = {"role": roles}
            if intent in ("click", "type"):
                query["ref_type"] = "element"
            elif intent in ("expand", "list-items", "extract"):
                query["ref_type"] = ["element", "container"]
            results = self._engine.search_snapshot(query, limit=10)
            for r in results:
                if r.get("ref") not in [c.get("ref") for c in candidates]:
                    candidates.append(r)

        if not candidates and target_hint:
            by_text = self._engine.find_by_text(target_hint)
            if roles:
                role_set = {r.lower() for r in roles}
                by_text = [
                    n for n in by_text
                    if str(n.get("role", "").lower()) in role_set
                ]
            for r in by_text:
                if r.get("ref") not in [c.get("ref") for c in candidates]:
                    candidates.append(r)

        near_hint = constraints.get("near")
        if near_hint:
            near_results = self._find_near_candidates(near_hint, constraints)
            for r in near_results:
                if r.get("ref") not in [c.get("ref") for c in candidates]:
                    candidates.append(r)

        return candidates

    def _find_near_candidates(
        self, near_hint: str, constraints: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        near_matches = self._engine.find_by_text(near_hint)
        if not near_matches:
            return []
        parent_ref = near_matches[0].get("parent_ref", "")
        if not parent_ref:
            return []
        roles = constraints.get("role") or []
        q: Dict[str, Any] = {"parent_ref": parent_ref, "ref_type": "element"}
        if roles:
            q["role"] = roles
        return self._engine.search_snapshot(q, limit=8)

    @staticmethod
    def _build_candidate_pack(
        candidates: List[Dict[str, Any]], intent: str, target_hint: str
    ) -> List[Dict[str, Any]]:
        pack = []
        seen_refs = set()
        for c in candidates:
            ref = c.get("ref", "")
            if ref in seen_refs:
                continue
            seen_refs.add(ref)
            entry = {
                "ref": ref,
                "role": c.get("role", ""),
                "name": c.get("name", ""),
                "text": c.get("text", ""),
                "tag": c.get("tag", ""),
                "ref_type": c.get("ref_type", ""),
                "in_viewport": c.get("in_viewport", True),
                "interactable_now": c.get("interactable_now", True),
                "parent_ref": c.get("parent_ref", ""),
                "confidence": TargetSelector._compute_confidence(
                    c, intent, target_hint
                ),
                "why_matched": TargetSelector._why_matched(c, intent, target_hint),
            }
            pack.append(entry)
        return pack[:8]

    @staticmethod
    def _compute_confidence(
        node: Dict[str, Any], intent: str, target_hint: str
    ) -> float:
        score = 0.5
        hint_lower = target_hint.lower() if target_hint else ""
        name = str(node.get("name", "")).lower()
        text = str(node.get("text", "")).lower()

        if hint_lower and (hint_lower in name or hint_lower in text):
            score += 0.3
        elif hint_lower and (name in hint_lower or text in hint_lower):
            score += 0.2
        elif hint_lower:
            hint_tokens = hint_lower.split()
            if any(t in name or t in text for t in hint_tokens if len(t) >= 2):
                score += 0.15
        if node.get("interactable_now") is True:
            score += 0.1
        if node.get("in_viewport") is True:
            score += 0.1
        return min(score, 1.0)

    @staticmethod
    def _why_matched(
        node: Dict[str, Any], intent: str, target_hint: str
    ) -> List[str]:
        reasons = []
        hint_lower = target_hint.lower() if target_hint else ""
        name = str(node.get("name", "")).lower()
        text = str(node.get("text", "")).lower()
        role = str(node.get("role", "")).lower()

        if hint_lower and (hint_lower in name or name in hint_lower):
            reasons.append("name_match")
        if hint_lower and (hint_lower in text or text in hint_lower):
            reasons.append("text_match")
        if role:
            reasons.append(f"role_{role}")
        if node.get("interactable_now"):
            reasons.append("interactable")
        if not reasons:
            reasons.append("partial_match")
        return reasons

    @staticmethod
    def _detect_conflicts(
        candidates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        if len(candidates) < 2:
            return []
        conflicts = []
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                ci = candidates[i]
                cj = candidates[j]
                if ci.get("confidence", 0) > 0.7 and cj.get("confidence", 0) > 0.7:
                    if ci.get("name") == cj.get("name") and ci.get("ref") != cj.get("ref"):
                        conflicts.append({
                            "type": "duplicate_name",
                            "candidates": [ci["ref"], cj["ref"]],
                            "name": ci.get("name"),
                        })
                    elif ci.get("text") == cj.get("text") and ci.get("ref") != cj.get("ref"):
                        conflicts.append({
                            "type": "duplicate_text",
                            "candidates": [ci["ref"], cj["ref"]],
                            "text": ci.get("text"),
                        })
        return conflicts

    def _deterministic_result(
        self, candidate: Dict[str, Any], intent: str
    ) -> Dict[str, Any]:
        ref = candidate["ref"]
        node = self._engine.get_ref(ref)
        return {
            "status": "selected",
            "target_ref": ref,
            "target_kind": candidate.get("ref_type", "element"),
            "skill_hint": intent,
            "selection_mode": "deterministic",
            "confidence": candidate.get("confidence", 0.8),
            "evidence": {
                "role": candidate.get("role", ""),
                "name": candidate.get("name", ""),
                "text": candidate.get("text", ""),
                "why_matched": candidate.get("why_matched", []),
                "source": "snapshot_index",
            },
            "alternatives": [],
            "approval_required": False,
        }
