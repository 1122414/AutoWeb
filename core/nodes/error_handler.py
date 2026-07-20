"""ErrorHandler 节点：全局错误处理与回退。"""

from __future__ import annotations

from typing import Literal

from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from core.state_v2 import AgentState
from core.nodes._verification import _build_verification_result
from prompts.verifier_prompts import ERROR_RECOVERY_PROMPT
from skills.logger import logger


def error_handler_node(state: AgentState, config: RunnableConfig, llm) -> Command[Literal["Observer", "__end__"]]:
    """
    [ErrorHandler] 全局错误处理与回退
    当其他节点发生不可恢复的错误时跳转至此
    """
    logger.info("\n🚑 [ErrorHandler] 检测到严重错误，正在尝试恢复...")

    error_msg = str(state.get("error") or "Unknown Error").strip()
    reflections = state.get("reflections", [])
    previous_error = str(state.get("_last_recovery_error") or "").strip()
    previous_count = int(state.get("_error_recovery_count") or 0)
    recovery_count = previous_count + 1 if error_msg == previous_error else 1
    base_updates = {
        "_error_recovery_count": recovery_count,
        "_last_recovery_error": error_msg,
        # 清除错误标志，以便重试或干净终止。
        "error": None,
    }

    if recovery_count >= 3:
        logger.info(
            "   ❌ ErrHandler: 同一严重错误连续 3 次，停止无界恢复。"
        )
        verification = _build_verification_result(
            is_success=False,
            is_done=True,
            summary=f"ErrorHandler 连续 3 次同类错误后终止: {error_msg}",
            source="error_handler",
            failure_scope="global",
            failed_action=state.get("plan", ""),
            evidence=error_msg,
            fix_hint="页面或浏览器状态持续不可用；保留错误证据并停止重复快照",
        )
        return Command(
            update={
                **base_updates,
                "messages": [
                    AIMessage(
                        content=(
                            "【系统故障】同一严重错误连续出现 3 次，"
                            "已停止自动恢复以避免无界循环。"
                        )
                    )
                ],
                "verification_result": verification,
                "is_complete": True,
            },
            goto="__end__",
        )

    # 构建回退策略
    prompt = ERROR_RECOVERY_PROMPT.format(
        error_msg=error_msg,
        last_reflection=reflections[-1] if reflections else 'None',
    )

    response = llm.invoke([HumanMessage(content=prompt)])
    content = str(response.content or "")

    is_terminate = "Status: TERMINATE" in content

    plan = state.get("plan", "")
    updates = {
        **base_updates,
        "messages": [AIMessage(content=f"【系统故障】正在恢复...\n{content}")],
    }

    if is_terminate:
        logger.info("   ❌ ErrHandler: 决定终止任务。")
        updates["verification_result"] = _build_verification_result(
            is_success=False,
            is_done=False,
            summary=f"ErrorHandler 终止: {error_msg}",
            source="error_handler",
            failure_scope="global",
            failed_action=plan,
            evidence=error_msg,
            fix_hint="当前错误不可恢复，任务终止",
        )
        updates["is_complete"] = True  # 虽然失败了，但也算结束
        return Command(update=updates, goto="__end__")
    else:
        logger.info("   🔄 ErrHandler: 尝试回退到 Observer 重新感知环境。")
        updates["verification_result"] = _build_verification_result(
            is_success=False,
            is_done=False,
            summary=f"ErrorHandler 回退: {error_msg}",
            source="error_handler",
            failure_scope="global",
            failed_action=plan,
            evidence=error_msg,
            fix_hint="回退到 Observer 重新分析页面状态",
        )
        return Command(update=updates, goto="Observer")
