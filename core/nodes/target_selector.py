"""
TargetSelector - dp_cli target ref confirmation layer (LangGraph node wrapper).

The pure TargetSelector class lives in skills/dpcli_target_selector.py so smoke
scripts and tests can import it without LangGraph/tiktoken dependencies.
"""
from __future__ import annotations

from typing import Literal

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from core.state_v2 import AgentState
from skills.dpcli_snapshot_store import SnapshotStore
from skills.dpcli_target_selector import TargetSelector, _normalize_target_constraints
from skills.logger import logger


def target_selector_node(
    state: AgentState, config: RunnableConfig
) -> Command[Literal["Coder", "Planner", "Observer", "ErrorHandler"]]:
    from config import DPCLI_ENABLED

    logger.info("\n[TargetSelector] selecting target element from snapshot...")

    if not DPCLI_ENABLED or state.get("execution_mode") == "python_code":
        return Command(
            update={"dpcli_target_result": {"status": "not_required"}},
            goto="Coder",
        )

    structured_plan = state.get("dpcli_structured_plan") or {}
    target_request = structured_plan.get("target_request") or {}
    if not target_request.get("required"):
        return Command(
            update={"dpcli_target_result": {"status": "not_required"}},
            goto="Coder",
        )

    intent = target_request.get("step_intent") or structured_plan.get(
        "step_intent", "click"
    )
    query = {
        "intent": intent,
        "target_hint": target_request.get("target_hint", ""),
        "target_constraints": _normalize_target_constraints(target_request),
    }
    capability_result = TargetSelector.select_from_capability_map(
        query,
        state.get("dpcli_agent_view") or {},
    )
    if capability_result is not None:
        logger.info(
            "   [TargetSelector] selected from capability map: "
            f"{capability_result.get('target_ref')}"
        )
        return Command(
            update={
                "dpcli_target_result": capability_result,
                "target_selection_conflict_count": 0,
            },
            goto="Coder",
        )

    snapshot_ref = state.get("dpcli_snapshot_ref")
    prev_result = state.get("dpcli_target_result") or {}
    if not snapshot_ref:
        if prev_result.get("status") == "need_more_observation":
            logger.info("   [TargetSelector] full snapshot unavailable after retry")
            return Command(
                update={
                    "dpcli_target_result": {
                        "status": "need_more_observation",
                        "reason": "full snapshot unavailable after retry",
                    },
                    "error": "full snapshot not available",
                    "error_type": "dpcli_snapshot_missing",
                },
                goto="ErrorHandler",
            )
        logger.info("   [TargetSelector] full snapshot unavailable, observing again")
        return Command(
            update={"dpcli_target_result": {"status": "need_more_observation"}},
            goto="Observer",
        )

    snapshot_session = snapshot_ref.get("session") or state.get("dpcli_session")
    selector = TargetSelector(
        store=SnapshotStore(session=str(snapshot_session)) if snapshot_session else None
    )
    result = selector.select(
        query=query,
        snapshot_ref=snapshot_ref,
    )

    status = result.get("status", "not_found")
    logger.info(
        f"   [TargetSelector] status={status}, "
        f"target_ref={result.get('target_ref', 'N/A')}"
    )

    if status == "selected":
        return Command(
            update={
                "dpcli_target_result": result,
                "target_selection_conflict_count": 0,
            },
            goto="Coder",
        )

    if status in ("not_required", "not_found"):
        return Command(update={"dpcli_target_result": result}, goto="Coder")

    if status == "need_approval":
        previous_refs = [
            item.get("ref") for item in (prev_result.get("alternatives") or [])
        ]
        current_refs = [item.get("ref") for item in (result.get("alternatives") or [])]
        repeated = bool(current_refs) and current_refs == previous_refs
        conflict_count = (
            int(state.get("target_selection_conflict_count") or 0) + 1
            if repeated
            else 1
        )
        if conflict_count >= 2:
            logger.info("   [TargetSelector] repeated conflict; stopping arbitration loop")
            return Command(
                update={
                    "dpcli_target_result": result,
                    "target_selection_conflict_count": conflict_count,
                    "error": "target selector repeated the same ambiguous candidates",
                    "error_type": "dpcli_target_ambiguous",
                },
                goto="ErrorHandler",
            )
        logger.info("   [TargetSelector] multiple candidates need planner arbitration")
        return Command(
            update={
                "dpcli_target_result": result,
                "target_selection_conflict_count": conflict_count,
                "human_approval_required": True,
                "execution_result": (
                    "TargetSelector candidates conflict "
                    f"({result.get('approval_reason', 'unknown')}), "
                    "Planner should clarify the target and action."
                ),
            },
            goto="Planner",
        )

    if (
        status == "need_more_observation"
        and prev_result.get("status") == "need_more_observation"
    ):
        return Command(
            update={
                "dpcli_target_result": result,
                "error": result.get("reason") or "snapshot index not available",
                "error_type": "dpcli_snapshot_missing",
            },
            goto="ErrorHandler",
        )

    logger.info(f"   [TargetSelector] status={status}, observing again")
    return Command(update={"dpcli_target_result": result}, goto="Observer")
