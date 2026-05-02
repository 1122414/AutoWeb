from __future__ import annotations

import traceback
from typing import Literal

from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from core.state_v2 import AgentState
from core.nodes._utils import _get_tab
from core.nodes._verification import _build_verification_result
from core.nodes._cache import _handle_cache_failure

from skills.actor import BrowserActor
from skills.logger import logger

def executor_node(state: AgentState, config: RunnableConfig) -> Command[Literal["Verifier", "Coder", "Planner", "Observer", "ErrorHandler"]]:
    """[Executor] 执行代码，并根据 _code_source 和错误类型进行分类路由"""
    logger.info("\n⚡ [Executor] 正在执行代码...")
    if state.get("execution_mode") == "dp_cli":
        logger.info("   -> execution_mode=dp_cli, 使用结构化 action 执行")
        return _executor_dpcli_branch(state, config)

    tab = _get_tab(config)
    code = state.get("generated_code", "")
    code_source = state.get("_code_source", "llm")

    logger.info(f"   📦 代码来源: {code_source}")

    # 设置当前 URL，供 save_data 自动按域名分目录
    from skills.toolbox import set_current_url
    current_url = state.get("current_url", "")
    set_current_url(current_url)

    # 错误分类关键词
    SYNTAX_ERRORS = ["SyntaxError", "IndentationError",
                     "NameError", "TypeError", "AttributeError"]
    LOCATOR_ERRORS = ["ElementNotFound", "TimeoutException",
                      "NoSuchElement", "ElementNotInteractable", "StaleElement"]
    browser = config["configurable"].get("browser")
    actor = BrowserActor(tab, browser)

    from skills.code_guard import scan_code_safety
    guard_result = scan_code_safety(code)
    if not guard_result.get("is_safe", True):
        reasons = guard_result.get("reasons", [])
        reason_text = "; ".join(reasons) if reasons else "未知安全风险"
        logger.warning(f"   🚫 [SecurityGuard] 拦截执行: {reason_text}")

        if code_source == "cache":
            return _handle_cache_failure(state, {
                "messages": [AIMessage(content=f"【缓存代码安全拦截】{reason_text}")],
                "execution_log": f"SecurityGuard Blocked: {reason_text}",
                "reflections": [f"缓存代码触发安全拦截: {reason_text}"],
                "verification_result": _build_verification_result(
                    is_success=False,
                    is_done=False,
                    summary="缓存代码被安全检查拦截",
                    source="executor",
                    failure_scope="local",
                    failed_action=state.get("plan", ""),
                    evidence=reason_text,
                    fix_hint="改用安全代码并避免危险模块/系统调用",
                ),
            })

        coder_retry = state.get("coder_retry_count", 0)
        if coder_retry < 3:
            return Command(
                update={
                    "messages": [AIMessage(content=f"【安全拦截】{reason_text}")],
                    "execution_log": f"SecurityGuard Blocked: {reason_text}",
                    "coder_retry_count": coder_retry + 1,
                    "error_type": "security",
                    "reflections": [f"安全拦截: {reason_text}，需要生成无危险调用的代码"],
                    "verification_result": _build_verification_result(
                        is_success=False,
                        is_done=False,
                        summary="代码被安全检查拦截",
                        source="executor",
                        failure_scope="local",
                        failed_action=state.get("plan", ""),
                        evidence=reason_text,
                        fix_hint="删除危险模块导入与系统级调用，仅保留页面自动化逻辑",
                    ),
                },
                goto="Coder"
            )

        return Command(
            update={
                "messages": [AIMessage(content=f"【安全拦截超限】{reason_text}")],
                "execution_log": f"SecurityGuard Blocked: {reason_text}",
                "error": f"Security guard blocked code after 3 retries: {reason_text}",
                "error_type": "security_max_retry",
                "verification_result": _build_verification_result(
                    is_success=False,
                    is_done=False,
                    summary="安全拦截重试超限",
                    source="executor",
                    failure_scope="local",
                    failed_action=state.get("plan", ""),
                    evidence=reason_text,
                    fix_hint="人工审查计划与代码，再继续执行",
                ),
            },
            goto="ErrorHandler"
        )

    try:
        # 执行代码
        exec_output = actor.execute_python_strategy(
            code, {"goal": state["user_task"]})
        execution_log = exec_output.get("execution_log", "")

        logger.info(f"   -> Log Length: {len(execution_log)}")

        # 检查执行日志中是否有错误（即使没有抛异常）
        error_in_log = None
        for kw in SYNTAX_ERRORS:
            if kw in execution_log:
                error_in_log = ("syntax", kw)
                break
        if not error_in_log:
            for kw in LOCATOR_ERRORS:
                if kw in execution_log:
                    error_in_log = ("locator", kw)
                    break

        if error_in_log:
            error_type, error_kw = error_in_log
            logger.info(f"   ⚠️ 检测到 {error_type} 错误: {error_kw}")

            # 缓存代码失败：失效缓存 + 跳 Planner
            if code_source == "cache":
                return _handle_cache_failure(state, {
                    "messages": [AIMessage(content=f"【缓存代码失败】{error_kw}，重新规划")],
                    "execution_log": execution_log,
                    "reflections": [f"缓存代码失败: {error_kw}，需要重新生成"],
                    "verification_result": _build_verification_result(
                        is_success=False,
                        is_done=False,
                        summary=f"缓存代码执行失败: {error_kw}",
                        source="executor",
                        failure_scope="local",
                        failed_action=state.get("plan", ""),
                        evidence=error_kw,
                        fix_hint="改用新代码或调整定位器，避免继续复用失败缓存",
                    ),
                })

            # LLM 代码的错误处理逻辑保持不变
            if error_type == "syntax":
                # 语法错误：微循环回 Coder
                coder_retry = state.get("coder_retry_count", 0)
                if coder_retry < 3:
                    logger.info(f"   🔄 语法错误，回 Coder 重试 ({coder_retry + 1}/3)")
                    return Command(
                        update={
                            "messages": [AIMessage(content=f"【语法错误】{error_kw}\n{execution_log[-500:]}")],
                            "execution_log": execution_log,
                            "coder_retry_count": coder_retry + 1,
                            "error_type": "syntax",
                            "reflections": [f"语法错误: {error_kw}，需要修复代码"]
                        },
                        goto="Coder"
                    )
                else:
                    logger.info(f"   ❌ 语法错误重试次数已达上限，转 ErrorHandler")
                    return Command(
                        update={
                            "messages": [AIMessage(content=f"【语法错误超限】{execution_log[-500:]}")],
                            "execution_log": execution_log,
                            "error": f"Syntax error after 3 retries: {error_kw}",
                            "error_type": "syntax_max_retry",
                            "verification_result": _build_verification_result(
                                is_success=False,
                                is_done=False,
                                summary=f"语法错误重试超限: {error_kw}",
                                source="executor",
                                failure_scope="local",
                                failed_action=state.get("plan", ""),
                                evidence=error_kw,
                                fix_hint="保留当前计划，只修复本步代码语法问题",
                            ),
                        },
                        goto="ErrorHandler"
                    )
            else:
                # 定位错误：走 ErrorHandler
                logger.info(f"   ❌ 定位错误，转 ErrorHandler")
                return Command(
                    update={
                        "messages": [AIMessage(content=f"【定位错误】{error_kw}\n{execution_log[-500:]}")],
                        "execution_log": execution_log,
                        "error": f"Locator error: {error_kw}",
                        "error_type": "locator",
                        "reflections": [f"定位错误: {error_kw}，需要重新分析页面"],
                        "verification_result": _build_verification_result(
                            is_success=False,
                            is_done=False,
                            summary=f"定位失败: {error_kw}",
                            source="executor",
                            failure_scope="local",
                            failed_action=state.get("plan", ""),
                            failed_locator=error_kw,
                            evidence=execution_log[-300:],
                            fix_hint="仅修复失败定位器，不要全局改写流程",
                        ),
                    },
                    goto="ErrorHandler"
                )

        # 执行成功
        return Command(
            update={
                "messages": [AIMessage(content=f"【执行报告】\n{execution_log}")],
                "execution_log": execution_log,
                "coder_retry_count": 0,  # 重置重试计数
                "error_type": None
            },
            goto="Verifier"
        )

    except Exception as e:
        error_msg = f"Critical Execution Error: {str(e)}"
        logger.info(f"   ❌ {error_msg}")
        traceback.print_exc()

        # 跳转到 ErrorHandler
        return Command(
            update={
                "messages": [AIMessage(content=f"【执行崩溃】\n{error_msg}")],
                "execution_log": error_msg,
                "error": str(e),
                "error_type": "critical",
                "reflections": [f"Execution crashed: {str(e)}"],
                "verification_result": _build_verification_result(
                    is_success=False,
                    is_done=False,
                    summary=f"执行崩溃: {str(e)}",
                    source="executor",
                    failure_scope="global",
                    failed_action=state.get("plan", ""),
                    evidence=error_msg,
                    fix_hint="先恢复执行环境，再回到当前失败步骤修复",
                ),
            },
            goto="ErrorHandler"
        )