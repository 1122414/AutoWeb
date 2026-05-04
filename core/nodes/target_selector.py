"""
TargetSelector - dp_cli target ref confirmation layer (LangGraph node wrapper).

The pure TargetSelector class lives in skills/dpcli_target_selector.py
to allow smoke scripts and tests to import it without LangGraph/tiktoken deps.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from skills.dpcli_target_selector import TargetSelector, _normalize_target_constraints
from skills.logger import logger


# =============================================================================
# LangGraph Node
# =============================================================================

from typing import Literal  # noqa: E402

from langchain_core.runnables import RunnableConfig  # noqa: E402
from langgraph.types import Command  # noqa: E402

from core.state_v2 import AgentState  # noqa: E402


def target_selector_node(
    state: AgentState, config: RunnableConfig
) -> Command[Literal["Coder", "Planner", "Observer", "ErrorHandler"]]:
    from config import DPCLI_ENABLED

    logger.info("\n🎯 [TargetSelector] 正在匹配页面目标元素...")

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

    snapshot_ref = state.get("dpcli_snapshot_ref")
    if not snapshot_ref:
        prev_result = state.get("dpcli_target_result") or {}
        if prev_result.get("status") == "need_more_observation":
            logger.info("   ⚠️ [TargetSelector] full snapshot 二次不可用，进入 ErrorHandler")
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
        logger.info("   🔄 [TargetSelector] full snapshot 不可用，返回 Observer 重新观察")
        return Command(
            update={"dpcli_target_result": {"status": "need_more_observation"}},
            goto="Observer",
        )

    selector = TargetSelector()
    intent = target_request.get("step_intent") or structured_plan.get(
        "step_intent", "click"
    )

    result = selector.select(
        query={
            "intent": intent,
            "target_hint": target_request.get("target_hint", ""),
            "target_constraints": _normalize_target_constraints(target_request),
        },
        snapshot_ref=snapshot_ref,
    )

    status = result.get("status", "not_found")
    logger.info(
        f"   📊 [TargetSelector] 状态: {status}, "
        f"target_ref: {result.get('target_ref', 'N/A')}"
    )

    if status == "selected":
        return Command(
            update={"dpcli_target_result": result},
            goto="Coder",
        )

    if status == "not_required" or status == "not_found":
        return Command(
            update={"dpcli_target_result": result},
            goto="Coder",
        )

    if status == "need_approval":
        logger.info("   ⚠️ [TargetSelector] 候选冲突，返回 Planner 写入审批信号")
        return Command(
            update={
                "dpcli_target_result": result,
                "human_approval_required": True,
                "execution_result": (
                    f"TargetSelector 候选冲突 ({result.get('approval_reason', 'unknown')})，"
                    "请 Planner 决定下一个 action"
                ),
            },
            goto="Planner",
        )

    logger.info(f"   ⚠️ [TargetSelector] 状态 {status}，返回 Observer")
    return Command(
        update={"dpcli_target_result": result},
        goto="Observer",
    )
