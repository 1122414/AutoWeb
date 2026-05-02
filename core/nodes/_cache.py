from __future__ import annotations

from core.state_v2 import AgentState
from core.nodes._locators import _extract_domain_key_from_url, _extract_locator_info, _build_step_context
from langgraph.types import Command

from core.nodes._verification import _coerce_verification_result
from skills.logger import logger

def _save_code_to_cache(state: AgentState, current_url: str):
    """
    [辅助函数] 将验证通过的代码存入缓存

    存储条件:
    - 步骤成功
    - 非缓存命中执行 (避免重复存储)
    - 代码长度足够 (>50 字符)
    """
    from config import CODE_CACHE_ENABLED

    if not CODE_CACHE_ENABLED:
        return {"false": "[CodeCache] 缓存已禁用"}

    # 如果是缓存代码执行成功，不重复存储
    code_source = state.get("_code_source")
    if code_source == "cache":
        logger.info("   ⏭️ [CodeCache] 缓存代码执行，跳过存储")
        return {"false": "[CodeCache] 缓存代码执行，跳过存储"}

    code = state.get("generated_code", "")
    if not code or len(code) < 50:
        logger.info("   ⏭️ [CodeCache] 代码过短，跳过存储")
        return {"false": "[CodeCache] 代码过短，跳过存储"}

    # 使用 plan 作为 goal
    goal = state.get("plan", "")
    dom_skeleton = state.get("dom_skeleton", "")

    try:
        from skills.code_cache import code_cache_manager

        is_submitted = code_cache_manager.save(
            goal=goal,
            dom_skeleton=dom_skeleton,
            url=current_url,
            code=code,
            user_task=state.get("user_task", ""),
            locator_info=_extract_locator_info(state),
        )

        if is_submitted:
            logger.info(f" 💾 [CodeCache] 存储任务已提交后台")
            return {"true": "[CodeCache] 任务已提交"}  # 这里不再返回具体的 ID
        else:
            logger.info("   ⚠️ [CodeCache] 存储失败，纯导航代码")
            return {"false": "[CodeCache] 存储失败，纯导航代码"}
    except Exception as e:
        logger.info(f"   ⚠️ [CodeCache] 存储失败: {e}")
        return {"false": f"[CodeCache] 存储失败: {e}"}


def _save_dom_to_cache(state: AgentState, current_url: str):
    """
    [辅助函数] 将验证通过的策略存入 DomCache
    """
    from config import DOM_CACHE_ENABLED

    if not DOM_CACHE_ENABLED:
        return {"false": "[DomCache] 缓存已禁用"}

    observer_source = state.get("_observer_source")
    if observer_source == "dom_cache":
        logger.info("   ⏭️ [DomCache] 本轮策略来自缓存，跳过存储")
        return {"false": "策略来自缓存"}

    # 取最新的一条策略
    locator_suggestions = state.get("locator_suggestions", [])
    if not locator_suggestions:
        return {"false": "无策略"}

    latest_strategy = locator_suggestions[-1]
    strategies = latest_strategy.get("strategies", [])
    if isinstance(strategies, dict):
        strategies = [strategies]

    if not strategies:
        return {"false": "无策略详情"}

    task = state.get("user_task", "")
    dom = state.get("dom_skeleton", "")
    step_context = _build_step_context(state.get("finished_steps", []))

    try:
        from skills.dom_cache import dom_cache_manager
        dom_cache_manager.save(
            user_task=task,
            current_url=current_url,
            dom_skeleton=dom,
            locator_suggestions=strategies,
            step_context=step_context,
        )
        logger.info("   💾 [DomCache] 已提交缓存写入任务")
        return {"true": "[DomCache] 任务已提交"}
    except Exception as e:
        logger.info(f"   ⚠️ [DomCache] 存储失败: {e}")
        return {"false": f"存储失败: {e}"}


def _record_cache_failure(cache_type: str, cache_id: str, domain_key: str, reason: str) -> None:
    """统一记录缓存失败：更新 manager 统计 + 标记软黑名单"""
    try:
        from skills.cache_blacklist import cache_soft_blacklist
        if cache_type == "codecache":
            from skills.code_cache import code_cache_manager
            code_cache_manager.record_failure(cache_id, reason=reason)
        elif cache_type == "domcache":
            from skills.dom_cache import dom_cache_manager
            dom_cache_manager.record_failure(cache_id, reason=reason)
        else:
            return
        cache_soft_blacklist.mark_failed(
            cache_type=cache_type,
            domain_key=domain_key,
            cache_id=cache_id,
            reason=reason,
        )
    except Exception as e:
        logger.info(f"   ⚠️ [{cache_type}] 记录失败异常: {e}")


def _handle_cache_failure(state: AgentState, updates: dict) -> Command:
    """缓存代码失败统一处理：记录失败 + 标记熔断 + 跳 Planner

    调用方负责构建 updates 中的 messages / reflections 等字段，
    本函数只负责：记录失败 + 追加熔断标记。
    """
    cache_hit_id = state.get("_cache_hit_id", "")
    failed_cache_ids = list(state.get("_failed_code_cache_ids", []) or [])
    if cache_hit_id:
        _record_cache_failure(
            "codecache", cache_hit_id,
            _extract_domain_key_from_url(state.get("current_url", "")),
            "执行/验收失败"
        )
        if cache_hit_id not in failed_cache_ids:
            failed_cache_ids.append(cache_hit_id)

    updates["verification_result"] = _coerce_verification_result(
        updates.get("verification_result"),
        fallback_is_success=False,
        fallback_summary="缓存代码执行/验收失败，需要重新规划",
        fallback_source="executor",
        fallback_failure_scope="local",
        fallback_failed_action=state.get("plan", ""),
        fallback_evidence=updates.get("error", "") or (
            updates.get("reflections", [
                        ""])[-1] if updates.get("reflections") else ""
        ),
        fallback_fix_hint="更换定位器或改用新的执行方案，避免复用本次失败缓存",
    )
    updates["_cache_failed_this_round"] = True
    updates["_cache_hit_id"] = None
    updates["_failed_code_cache_ids"] = failed_cache_ids
    return Command(update=updates, goto="Planner")