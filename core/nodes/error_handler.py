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

    error_msg = state.get("error", "Unknown Error")
    reflections = state.get("reflections", [])

    # 构建回退策略
    prompt = ERROR_RECOVERY_PROMPT.format(
        error_msg=error_msg,
        last_reflection=reflections[-1] if reflections else 'None',
    )

    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content

    is_terminate = "Status: TERMINATE" in content

    plan = state.get("plan", "")
    updates = {
        "messages": [AIMessage(content=f"【系统故障】正在恢复...\n{content}")],
        # 清除错误标志，以便重试
        "error": None,
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
