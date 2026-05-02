from __future__ import annotations

from typing import Literal

from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from core.state_v2 import AgentState
from core.nodes._dpcli import (
    _should_use_dpcli_action,
    _dpcli_action_context,
    _state_has_dpcli_refs,
    _validate_dpcli_action,
)
from prompts.coder_prompts import ACTION_CODE_GEN_PROMPT, CODER_TASK_WRAPPER
from skills.logger import logger

def coder_node(state: AgentState, config: RunnableConfig, llm) -> Command[Literal["Executor", "Coder"]]:
    """[Coder] 编写代码"""
    logger.info("\n💻 [Coder] 正在编写代码...")

    if _should_use_dpcli_action(state):
        logger.info("   -> Coder 使用 dp_cli action JSON 模式")
        return _dpcli_action_coder_node(state, config, llm)

    plan = state.get("plan", "")

    # 获取累积的定位策略列表，序列化为 JSON 字符串
    accumulated_strategies = state.get("locator_suggestions", [])
    if accumulated_strategies:
        xpath_plan = json.dumps(accumulated_strategies,
                                ensure_ascii=False, indent=2)
        logger.info(f"   -> Coder 收到 {len(accumulated_strategies)} 个页面的定位策略")
    else:
        xpath_plan = "无定位策略"

    # 构建 Prompt
    base_prompt = ACTION_CODE_GEN_PROMPT.format(
        xpath_plan=xpath_plan,
    )

    prompt = CODER_TASK_WRAPPER.format(plan=plan, base_prompt=base_prompt)

    # 注意：不使用 bind_tools，因为会导致 LLM 返回 tool_calls 而不是生成代码
    response = llm.invoke([HumanMessage(content=prompt)])

    # 代码提取逻辑
    content = response.content
    code = ""
    if "```python" in content:
        code = content.split("```python")[1].split("```")[0].strip()
    elif "```" in content:
        code = content.split("```")[1].split("```")[0].strip()
    else:
        code = content

    return Command(
        update={
            "messages": [AIMessage(content=f"【代码生成】\n{response.content}")],
            "generated_code": code,
            "generated_action": None,
            "execution_mode": "python_code",
            "_code_source": "llm",
            "_action_source": None,
        },
        goto="Executor"
    )


def _dpcli_result_url(result: Dict[str, Any]) -> str:
    data = result.get("data") if isinstance(result, dict) else {}
    if not isinstance(data, dict):
        return ""
    page = data.get("page")
    if isinstance(page, dict):
        return str(page.get("url") or "")
    return ""


def _dpcli_error(result: Dict[str, Any]) -> Dict[str, Any]:
    error = result.get("error") if isinstance(result, dict) else {}
    return error if isinstance(error, dict) else {"code": "unknown", "message": str(error or "")}


def _dpcli_failure_goto(error_code: str) -> str:
    if error_code in {"ref_stale", "ref_not_found", "element_not_found", "element_not_interactable"}:
        return "Observer"
    if error_code in {"invalid_ref_type", "invalid_input", "invalid_action"}:
        return "Coder"
    return "ErrorHandler"


def _executor_dpcli_branch(state: AgentState, config: RunnableConfig) -> Command:
    action = state.get("generated_action")
    if not isinstance(action, dict):
        reason = "generated_action is missing or not a JSON object"
        return Command(
            update={
                "messages": [AIMessage(content=f"【dp_cli动作无效】{reason}")],
                "execution_log": reason,
                "error_type": "dpcli_invalid_action",
                "verification_result": _build_verification_result(
                    is_success=False,
                    is_done=False,
                    summary=reason,
                    source="executor",
                    failure_scope="local",
                    failed_action=str(action),
                    evidence=reason,
                    fix_hint="重新生成结构化 dp_cli action JSON",
                ),
            },
            goto="Coder",
        )

    from config import DPCLI_HEADLESS, DPCLI_SESSION
    from skills.dpcli_executor import DPCLIExecutor

    session = state.get("dpcli_session") or DPCLI_SESSION
    executor = DPCLIExecutor(session=session, headless=DPCLI_HEADLESS)
    result = executor.execute_action(action)
    result_log = json.dumps(result, ensure_ascii=False, indent=2)
    current_url = _dpcli_result_url(result) or state.get("current_url", "")
    update: Dict[str, Any] = {
        "messages": [AIMessage(content=f"【dp_cli执行报告】\n{result_log}")],
        "execution_log": result_log,
        "dpcli_result": result,
        "dpcli_session": session,
        "current_url": current_url,
    }
    if result.get("action") == "snapshot" and result.get("ok"):
        update["dpcli_snapshot"] = result

    if result.get("ok"):
        update.update({
            "coder_retry_count": 0,
            "error_type": None,
        })
        return Command(update=update, goto="Verifier")

    error = _dpcli_error(result)
    error_code = str(error.get("code") or "unknown")
    route = _dpcli_failure_goto(error_code)
    params = action.get("params") or {}
    update.update({
        "error": str(error.get("message") or error_code),
        "error_type": f"dpcli_{error_code}",
        "reflections": [f"dp_cli action failed: {error_code}"],
        "verification_result": _build_verification_result(
            is_success=False,
            is_done=False,
            summary=f"dp_cli action failed: {error_code}",
            source="executor",
            failure_scope="local",
            failed_action=json.dumps(action, ensure_ascii=False),
            failed_locator=str(params.get("ref") or params.get("locator") or ""),
            evidence=json.dumps(error, ensure_ascii=False),
            fix_hint="ref 失效或元素不可用时请重新 snapshot；参数无效时重新生成 action",
        ),
    })
    if state.get("_action_source") == "action_cache":
        failed_ids = list(state.get("_failed_action_cache_ids", []) or [])
        hit_id = state.get("_action_cache_hit_id")
        if hit_id and hit_id not in failed_ids:
            failed_ids.append(hit_id)
        update["_failed_action_cache_ids"] = failed_ids
        try:
            from skills.action_cache import action_cache_manager
            if hit_id:
                action_cache_manager.record_failure(hit_id, reason=error_code)
        except Exception as cache_exc:
            logger.info(f"   ⚠️ [ActionCache] 记录失败异常: {cache_exc}")
    return Command(update=update, goto=route)