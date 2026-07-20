"""Coder 节点：代码生成（Python 代码 或 dp_cli Action JSON）。"""

from __future__ import annotations

import json
from typing import Any, Dict, Literal

from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from core.state_v2 import AgentState
from core.nodes._verification import _build_verification_result
from core.nodes._dpcli import (
    _should_use_dpcli_action,
    _dpcli_action_context,
    _validate_dpcli_action,
    _extract_json_object,
    _dpcli_result_url,
    _dpcli_error,
    _dpcli_failure_goto,
    _dpcli_action_kind,
    _compact_dpcli_result_for_log,
    _compact_dpcli_snapshot,
    _dpcli_policy_action_from_structured_plan,
)
from prompts.coder_prompts import ACTION_CODE_GEN_PROMPT, CODER_TASK_WRAPPER
from prompts.dpcli_action_prompts import DPCLI_ACTION_GEN_PROMPT
from skills.logger import logger


def coder_node(state: AgentState, config: RunnableConfig, llm) -> Command[Literal["Executor", "Coder"]]:
    """[Coder] 编写代码"""
    logger.info("\n💻 [Coder] 正在编写代码...")

    if _should_use_dpcli_action(state):
        logger.info("   -> Coder 使用 dp_cli action JSON 模式")
        return _dpcli_action_coder_node(state, config, llm)

    plan = state.get("plan", "")

    accumulated_strategies = state.get("locator_suggestions", [])
    if accumulated_strategies:
        xpath_plan = json.dumps(accumulated_strategies,
                                ensure_ascii=False, indent=2)
        logger.info(f"   -> Coder 收到 {len(accumulated_strategies)} 个页面的定位策略")
    else:
        xpath_plan = "无定位策略"

    base_prompt = ACTION_CODE_GEN_PROMPT.format(
        xpath_plan=xpath_plan,
    )

    prompt = CODER_TASK_WRAPPER.format(plan=plan, base_prompt=base_prompt)

    response = llm.invoke([HumanMessage(content=prompt)])

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


def _dpcli_action_coder_node(state: AgentState, config: RunnableConfig, llm) -> Command:
    policy_action = _dpcli_policy_action_from_structured_plan(state)
    if policy_action:
        validation_error = _validate_dpcli_action(policy_action, state)
        if not validation_error:
            policy_log = json.dumps(policy_action, ensure_ascii=False, indent=2)
            return Command(
                update={
                    "messages": [AIMessage(content=f"[dp_cli policy action]\n{policy_log}")],
                    "generated_action": policy_action,
                    "generated_code": None,
                    "execution_mode": "dp_cli",
                    "coder_retry_count": 0,
                    "_action_source": "policy",
                    "_code_source": None,
                    "_dpcli_action_disabled": False,
                },
                goto="Executor",
            )

    plan = state.get("plan", "")
    prompt = CODER_TASK_WRAPPER.format(
        plan=plan,
        base_prompt=DPCLI_ACTION_GEN_PROMPT.replace(
            "{context}", _dpcli_action_context(state)),
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    action = _extract_json_object(getattr(response, "content", response))
    validation_error = _validate_dpcli_action(action or {}, state)

    if validation_error:
        retry_count = state.get("coder_retry_count", 0)
        if retry_count < 2:
            return Command(
                update={
                    "messages": [AIMessage(content=f"【dp_cli action生成失败】{validation_error}")],
                    "coder_retry_count": retry_count + 1,
                    "execution_mode": "dp_cli",
                    "error_type": "dpcli_action_json",
                    "reflections": [f"dp_cli action JSON invalid: {validation_error}"],
                },
                goto="Coder",
            )
        logger.info("   ⚠️ dp_cli action 连续生成失败，返回 Planner 重新规划")
        return Command(update={
            "execution_mode": "dp_cli",
            "generated_action": None,
            "_action_source": None,
            "_dpcli_action_disabled": False,
            "error_type": "dpcli_action_json",
            "execution_result": f"dp_cli action generation failed after {retry_count + 1} attempts: {validation_error}",
            "reflections": state.get("reflections", []) + [
                f"dp_cli action JSON invalid after retries: {validation_error}"
            ],
        }, goto="Planner")

    return Command(
        update={
            "messages": [AIMessage(content=f"【dp_cli action生成】\n{json.dumps(action, ensure_ascii=False, indent=2)}")],
            "generated_action": action,
            "generated_code": None,
            "execution_mode": "dp_cli",
            "coder_retry_count": 0,
            "_action_source": "llm",
            "_code_source": None,
            "_dpcli_action_disabled": False,
        },
        goto="Executor",
    )


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
    before_url = state.get("current_url", "")
    executor = DPCLIExecutor(session=session, headless=DPCLI_HEADLESS)
    result = executor.execute_action(action)
    if str(action.get("skill") or "").lower() == "extract":
        from skills.dpcli_result_enricher import enrich_extract_result

        result = enrich_extract_result(state, action, result)
    result_log = json.dumps(
        _compact_dpcli_result_for_log(result),
        ensure_ascii=False,
        indent=2,
    )
    current_url = _dpcli_result_url(result) or state.get("current_url", "")
    url_changed = bool(before_url and current_url and before_url != current_url)
    update: Dict[str, Any] = {
        "messages": [AIMessage(content=f"【dp_cli执行报告】\n{result_log}")],
        "execution_log": result_log,
        "dpcli_result": result,
        "dpcli_session": session,
        "current_url": current_url,
        "dpcli_execution_evidence": {
            "before_url": before_url,
            "after_url": current_url,
            "url_changed": url_changed,
            "action_skill": action.get("skill", ""),
            "result_ok": bool(result.get("ok")),
        },
    }
    if result.get("action") == "snapshot" and result.get("ok"):
        update["dpcli_snapshot"] = _compact_dpcli_snapshot(result)

    if result.get("ok"):
        action_kind = _dpcli_action_kind(action)
        is_observation = action_kind == "observation"
        update.update({
            "coder_retry_count": 0,
            "error_type": None,
            "dpcli_action_kind": action_kind,
            "dpcli_verification_contract": {
                "action_kind": action_kind,
                "page_effect_expected": not is_observation,
                "url_change_expected": not is_observation,
                "dom_change_expected": not is_observation,
            },
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
