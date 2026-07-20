from __future__ import annotations

import time

from typing import Literal

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from core.state_v2 import AgentState
from core.nodes._utils import _parse_iso_datetime
from core.nodes._locators import (
    _extract_locator_info,
    _extract_locator_candidates,
    _extract_locators_from_strategies,
    _has_locator_overlap,
    _dry_run_cache_hit_locators,
    _extract_domain_key_from_url,
)
from core.nodes._cache import _record_cache_failure
from core.nodes._verification import _build_verification_result
from skills.logger import logger


def _govern_cache_hits(
    kind,
    hits,
    *,
    threshold,
    failed_ids,
    task_started_at,
):
    """Apply one admission interface to heterogeneous cache adapters."""
    from config import CACHE_GOVERNANCE_ENABLED

    if not CACHE_GOVERNANCE_ENABLED:
        return [
            hit
            for hit in hits
            if (not getattr(hit, "id", ""))
            or getattr(hit, "id", "") not in set(failed_ids or [])
        ]
    from skills.cache_governance import cache_governance

    eligible, decisions = cache_governance.filter_hits(
        kind,
        hits,
        threshold=threshold,
        failed_ids=failed_ids,
        task_started_at=task_started_at,
    )
    rejected = {}
    for decision in decisions:
        if decision.allowed:
            continue
        rejected[decision.reason] = rejected.get(decision.reason, 0) + 1
    if rejected:
        logger.info(f"   ⏭️ [CacheGovernance] {kind} rejected={rejected}")
    return eligible


def cache_lookup_node(state: AgentState, config: RunnableConfig) -> Command[Literal["Coder", "Executor", "Observer"]]:
    """
    [CacheLookup] 尝试从缓存中检索可复用的代码

    策略:
    - 检查 _cache_failed_this_round，若为 True 则强制跳过
    - 使用 plan + task + dom_skeleton + url 构建检索 Query
    - 命中时设置 _code_source = "cache"，跳到 Executor
    - 未命中时设置 _code_source = "llm"，跳到 Coder
    """
    from config import (
        CODE_CACHE_DRY_RUN_ENABLED,
        CODE_CACHE_DRY_RUN_TIMEOUT_SECONDS,
        CODE_CACHE_ENABLED,
        CODE_CACHE_THRESHOLD,
        ACTION_CACHE_ENABLED,
        ACTION_CACHE_THRESHOLD,
        DPCLI_ENABLED,
    )

    # 检查本轮是否已有缓存失败（防止死循环）
    if state.get("_cache_failed_this_round"):
        logger.info("⚠️ [CacheLookup] 本轮缓存已失败，强制跳过")
        return Command(
            update={"_code_source": "llm"},
            goto="Coder"
        )

    # 检查是否启用缓存
    if not CODE_CACHE_ENABLED and not (DPCLI_ENABLED and ACTION_CACHE_ENABLED):
        logger.info("⏭️ [CacheLookup] 缓存已禁用，跳过检索")
        return Command(
            update={"_code_source": "llm"},
            goto="Coder"
        )

    logger.info("\n🔍 [CacheLookup] 正在检索可复用代码...")

    user_task = state.get("user_task", "")
    plan = state.get("plan", "")
    current_url = state.get("current_url", "")

    # 提取 Observer 的定位策略摘要
    locator_info = _extract_locator_info(state)

    # 空白页/初始页面，跳过缓存检索
    if not current_url or current_url.startswith(("about:", "data:", "chrome://")):
        logger.info("   ⏭️ 初始页面，跳过缓存检索")
        return Command(
            update={"_code_source": "llm"},
            goto="Coder"
        )

    if DPCLI_ENABLED and ACTION_CACHE_ENABLED:
        try:
            from skills.action_cache import action_cache_manager
            failed_action_ids = set(state.get("_failed_action_cache_ids", []) or [])
            hits = action_cache_manager.search(
                user_task=user_task,
                goal=plan,
                url=current_url,
                snapshot_view=state.get("dpcli_snapshot_view"),
                top_k=5,
            )
            hits = _govern_cache_hits(
                "action",
                hits,
                threshold=ACTION_CACHE_THRESHOLD,
                failed_ids=failed_action_ids,
                task_started_at=state.get("_task_started_at"),
            )
            if hits:
                best_hit = hits[0]
                logger.info(
                    f"✅ [ActionCache] 命中 action 缓存: score={best_hit.score:.4f}")
                return Command(
                    update={
                        "messages": [AIMessage(content=f"【ActionCache命中】复用历史 dp_cli action (Score: {best_hit.score:.4f})")],
                        "generated_action": best_hit.action,
                        "generated_code": None,
                        "execution_mode": "dp_cli",
                        "_action_source": "action_cache",
                        "_action_cache_hit_id": best_hit.id,
                        "_code_source": None,
                    },
                    goto="Executor",
                )
        except Exception as action_cache_exc:
            logger.info(f"   ⚠️ [ActionCache] 检索异常: {action_cache_exc}")

    if not CODE_CACHE_ENABLED:
        logger.info("   ⏭️ [CacheLookup] CodeCache 关闭且 ActionCache 未命中")
        return Command(update={"_code_source": "llm"}, goto="Coder")

    try:
        from skills.code_cache import code_cache_manager

        hits = code_cache_manager.search(
            user_task=user_task,
            goal=plan,
            url=current_url,
            locator_info=locator_info,
            top_k=10
        )

        failed_code_cache_ids = set(
            state.get("_failed_code_cache_ids", []) or [])
        task_started_at = _parse_iso_datetime(
            state.get("_task_started_at", ""))
        eligible_hits = _govern_cache_hits(
            "code",
            hits,
            threshold=CODE_CACHE_THRESHOLD,
            failed_ids=failed_code_cache_ids,
            task_started_at=task_started_at,
        )

        if eligible_hits:
            best_hit = eligible_hits[0]
            logger.info(
                f"✅ 命中缓存! Score: {best_hit.score:.4f}, URL: {best_hit.url_pattern}")
            logger.info(f"📋 原任务: {best_hit.goal[:50]}...")

            # 参数感知：检测任务差异，做程序化替换
            final_code = best_hit.code
            cached_task = best_hit.user_task
            from skills.code_cache import extract_param_diffs, apply_param_substitution

            diffs = []
            if cached_task and cached_task != user_task:
                diffs = extract_param_diffs(cached_task, user_task)

            if diffs:
                logger.info(f"🔄 [ParamSubst] 检测到参数差异: {diffs}")
                final_code = apply_param_substitution(best_hit.code, diffs)
                logger.info(
                    f"✅ [ParamSubst] 已替换 {len(diffs)} 个参数，零 LLM Token")

            # Stage-4: Dry-Run 微轮询探测，避免 SPA 懒加载导致假阴性
            if CODE_CACHE_DRY_RUN_ENABLED:
                # Code 阶段 Dry-Run 只使用代码侧定位信息（不混入 observer locator）
                loc_candidates = _extract_locator_candidates(
                    best_hit.locator_info,
                    final_code,
                )
                dry_run_started_at = time.time()
                logger.info(
                    f"   🔎 [CacheLookup] Dry-Run开始: candidates={len(loc_candidates)}"
                )
                dry_ok, failed_locator = _dry_run_cache_hit_locators(
                    config=config,
                    locator_candidates=loc_candidates,
                    timeout_seconds=CODE_CACHE_DRY_RUN_TIMEOUT_SECONDS,
                )
                dry_run_elapsed = time.time() - dry_run_started_at
                if dry_ok:
                    logger.info(
                        f"   ✅ [CacheLookup] Dry-Run通过: validated={len(loc_candidates)}, "
                        f"elapsed={dry_run_elapsed:.2f}s"
                    )
                if not dry_ok:
                    reason = f"Dry-Run失败: locator={failed_locator}"
                    logger.info(f"   ❌ [CacheLookup] {reason}")
                    failed_cache_ids = list(
                        state.get("_failed_code_cache_ids", []) or [])
                    failed_dom_cache_ids = list(
                        state.get("_failed_dom_cache_ids", []) or [])
                    if best_hit.id and best_hit.id not in failed_cache_ids:
                        failed_cache_ids.append(best_hit.id)

                    observer_source = state.get("_observer_source", "")
                    dom_cache_hit_id = state.get("_dom_cache_hit_id", "")
                    dom_dual_invalidate = False

                    if observer_source == "dom_cache" and dom_cache_hit_id:
                        strategies = []
                        loc_entries = state.get(
                            "locator_suggestions", []) or []
                        if loc_entries and isinstance(loc_entries[-1], dict):
                            strategies = loc_entries[-1].get("strategies", [])
                        dom_locators = _extract_locators_from_strategies(
                            strategies)
                        dom_dual_invalidate = _has_locator_overlap(
                            failed_locator,
                            dom_locators,
                        )

                    try:
                        domain_key = _extract_domain_key_from_url(current_url)
                        if best_hit.id:
                            _record_cache_failure("codecache", best_hit.id, domain_key, reason)

                        if dom_dual_invalidate and dom_cache_hit_id:
                            dom_reason = f"Code Dry-Run联动失效: locator={failed_locator}"
                            _record_cache_failure("domcache", dom_cache_hit_id, domain_key, dom_reason)
                            if dom_cache_hit_id not in failed_dom_cache_ids:
                                failed_dom_cache_ids.append(dom_cache_hit_id)
                    except Exception as mark_exc:
                        logger.info(
                            f"   ⚠️ [CacheLookup] Dry-Run 失败打标异常: {mark_exc}")

                    return Command(
                        update={
                            "messages": [AIMessage(content=f"【缓存Dry-Run失败】{reason}，回退Observer重建定位")],
                            "reflections": [f"缓存代码Dry-Run失败: {reason}"],
                            "verification_result": _build_verification_result(
                                is_success=False,
                                is_done=False,
                                summary=f"缓存Dry-Run失败: {reason}",
                                source="executor",
                                failure_scope="local",
                                failed_action=state.get("plan", ""),
                                failed_locator=failed_locator,
                                evidence=reason,
                                fix_hint="请由Observer重新生成定位策略，避免复用该缓存命中",
                            ),
                            "_cache_failed_this_round": True,
                            "_cache_hit_id": None,
                            "_failed_code_cache_ids": failed_cache_ids,
                            "_failed_dom_cache_ids": failed_dom_cache_ids,
                            "_code_source": "llm",
                        },
                        goto="Observer"
                    )

            # 直接使用缓存代码（替换后），跳到 Executor
            return Command(
                update={
                    "generated_code": final_code,
                    "messages": [AIMessage(content=f"【缓存命中】复用历史代码 (Score: {best_hit.score:.4f})")],
                    "_code_source": "cache",
                    "_cache_hit_id": best_hit.id,
                },
                goto="Executor"
            )
        else:
            if eligible_hits:
                logger.info(
                    f"   ❌ 最高分 {eligible_hits[0].score:.4f} 低于阈值 {CODE_CACHE_THRESHOLD}")
            elif hits:
                logger.info("   ❌ 命中均在失败黑名单，跳过缓存")
            else:
                logger.info("   ❌ 无匹配缓存")
            return Command(
                update={"_code_source": "llm"},
                goto="Coder"
            )

    except Exception as e:
        logger.info(f"   ⚠️ [CacheLookup] 检索异常: {e}")
        return Command(
            update={"_code_source": "llm"},
            goto="Coder"
        )
