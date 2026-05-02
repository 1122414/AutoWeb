from __future__ import annotations

import time
import hashlib
from typing import Literal

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from core.state_v2 import AgentState
from core.nodes._utils import _get_tab, _parse_iso_datetime, _is_hit_from_current_task, _detect_task_continuity
from core.nodes._locators import (
    _extract_domain_key_from_url,
    _build_step_context,
    _dry_run_observer_strategies,
)
from core.nodes._verification import _is_failed_verification, _verification_focus_text, _build_verification_result
from core.nodes._cache import _record_cache_failure
from core.nodes._dpcli import _observer_dpcli_snapshot
from skills.logger import logger

def observer_node(state: AgentState, config: RunnableConfig, observer) -> Command[Literal["Planner", "Observer", "ErrorHandler"]]:
    """[Observer] 环境感知节点：捕获 DOM 并生成定位策略"""
    logger.info("\n👁️ [Observer] 正在感知环境...")

    # 新一轮开始，重置缓存失败标记
    base_update = {
        "_cache_failed_this_round": False,
        "_observer_source": None,
        "_dom_cache_hit_id": None,
    }

    dpcli_command = _observer_dpcli_snapshot(state)
    if dpcli_command is not None:
        return dpcli_command

    # 获取浏览器实例
    browser = config["configurable"].get("browser")
    if not browser:
        logger.info("   ⚠️ 无浏览器实例，跳过观察")
        return Command(update=base_update, goto="Planner")

    # 先等待新标签页稳定，再获取最新标签页
    time.sleep(0.3)  # 短暂等待，让新标签页有时间创建

    # 重新获取最新标签页（处理新标签页打开的情况）
    tab = browser.latest_tab

    # 等待页面加载完成
    try:
        tab.wait.load_start()
        tab.wait(0.5)  # 额外等待确保 DOM 稳定
    except TimeoutError as e:
        logger.warning(f"Page load timeout: {e}")
    except Exception as e:
        logger.debug(f"Wait interrupted: {e}")

    # 在页面加载后再获取 URL（确保是新页面的 URL）
    current_url = tab.url if tab else ""
    loop_count = state.get("loop_count", 0)

    logger.info(f"   -> 当前标签页: {current_url[:60]}...")

    # [优化] 初始页面检测：空白页/Google首页无需 DOM 分析
    is_blank = not current_url or current_url.startswith(
        ("about:", "data:", "chrome://"))
    is_google_home = "google.com" in current_url and "/search" not in current_url

    if loop_count == 0 and (is_blank or is_google_home):
        logger.info("   ⏩ [Observer] 初始页面，跳过 DOM 分析")
        base_update["current_url"] = current_url
        return Command(update=base_update, goto="Planner")

    task = state.get("user_task", "")
    finished_steps = state.get("finished_steps", [])

    try:
        # 捕获 DOM 骨架
        dom = observer.capture_dom_skeleton(tab)[:50000]

        # DOM 变化检测
        current_dom_hash = hashlib.md5(dom.encode()).hexdigest()
        previous_dom_hash = state.get("dom_hash", "")

        # 获取历史累积的策略列表
        accumulated_strategies = state.get("locator_suggestions", [])
        failed_dom_cache_ids = list(
            state.get("_failed_dom_cache_ids", []) or [])
        task_started_at = _parse_iso_datetime(
            state.get("_task_started_at", ""))

        # 检查是否有失败记录，有则强制重新分析（之前的策略可能是错的）
        reflections = state.get("reflections", [])
        error_type = state.get("error_type")
        verification = state.get("verification_result", {}) or {}
        verification_failed = _is_failed_verification(verification)
        has_failure = verification_failed or len(
            reflections) > 0 or error_type is not None

        # 只有当 DOM 发生变化 或 存在失败记录时，才进行视觉分析
        should_analyze = (current_dom_hash != previous_dom_hash) or has_failure
        new_strategy_entry = None
        observer_dry_run_exhausted = False
        observer_dry_run_last_note = ""
        observer_dry_run_last_locator = ""
        observer_dry_run_failed_locators = []

        # DOM Cache: 如果上轮是 DomCache 命中且后续失败，记录失败（不删除，供用户审查）
        observer_source = state.get("_observer_source", "")
        dom_cache_hit_id = state.get("_dom_cache_hit_id", "")
        if has_failure and observer_source == "dom_cache" and dom_cache_hit_id:
            try:
                from config import DOM_CACHE_ENABLED
                if DOM_CACHE_ENABLED:
                    _record_cache_failure(
                        "domcache", dom_cache_hit_id,
                        _extract_domain_key_from_url(current_url),
                        "后续执行失败"
                    )
            except Exception as e:
                logger.info(f"   ⚠️ [DomCache] 记录失败异常: {e}")
            if dom_cache_hit_id not in failed_dom_cache_ids:
                failed_dom_cache_ids.append(dom_cache_hit_id)
                logger.info(f"   ⛔ [DomCache] 标记失败命中ID: {dom_cache_hit_id}")

        # DOM Cache: 仅在需要分析且无失败记录时尝试命中
        dom_cache_hit = None
        if should_analyze and not has_failure:
            try:
                from config import (
                    DOM_CACHE_DRY_RUN_ENABLED,
                    DOM_CACHE_DRY_RUN_TIMEOUT_SECONDS,
                    DOM_CACHE_ENABLED,
                    DOM_CACHE_THRESHOLD,
                    DOM_CACHE_TOP_K,
                )
                if DOM_CACHE_ENABLED:
                    from skills.dom_cache import dom_cache_manager
                    step_context = _build_step_context(finished_steps)
                    cache_hits = dom_cache_manager.search(
                        user_task=task,
                        current_url=current_url,
                        dom_skeleton=dom,
                        step_context=step_context,
                        top_k=max(DOM_CACHE_TOP_K, 8),
                    )
                    if failed_dom_cache_ids:
                        raw_len = len(cache_hits)
                        cache_hits = [
                            hit for hit in cache_hits
                            if (not hit.id) or (hit.id not in failed_dom_cache_ids)
                        ]
                        if len(cache_hits) < raw_len:
                            logger.info(
                                f"   ⏭️ [DomCache] 过滤失败缓存命中: {raw_len - len(cache_hits)} 条")
                    if task_started_at is not None and cache_hits:
                        raw_len = len(cache_hits)
                        cache_hits = [
                            hit for hit in cache_hits
                            if not _is_hit_from_current_task(hit.created_at, task_started_at)
                        ]
                        if len(cache_hits) < raw_len:
                            logger.info(
                                f"   ⏭️ [DomCache] 过滤同任务新写入缓存: {raw_len - len(cache_hits)} 条")
                    if cache_hits and cache_hits[0].score >= DOM_CACHE_THRESHOLD:
                        dom_cache_hit = cache_hits[0]
                        logger.info(
                            f"   ✅ [DomCache] 命中缓存 score={dom_cache_hit.score:.4f}, "
                            f"url={dom_cache_hit.url_pattern}"
                        )

                    # Dom 阶段前置 Dry-Run：只校验 DomCache 的定位策略
                    if dom_cache_hit and dom_cache_hit.locator_suggestions and DOM_CACHE_DRY_RUN_ENABLED:
                        dom_dry_run_started_at = time.time()
                        logger.info(
                            f"   🔎 [DomCache] Dry-Run开始: strategies={len(dom_cache_hit.locator_suggestions)}"
                        )
                        dom_dry_ok, dom_failed_locators, dom_validated_count = _dry_run_observer_strategies(
                            config=config,
                            strategies=dom_cache_hit.locator_suggestions,
                            timeout_seconds=DOM_CACHE_DRY_RUN_TIMEOUT_SECONDS,
                        )
                        dom_dry_run_elapsed = time.time() - dom_dry_run_started_at
                        if dom_dry_ok:
                            logger.info(
                                f"   ✅ [DomCache] Dry-Run通过: validated={dom_validated_count}, "
                                f"elapsed={dom_dry_run_elapsed:.2f}s"
                            )
                        if not dom_dry_ok:
                            failed_preview = "; ".join(dom_failed_locators[:5])
                            reason = (
                                f"Dom Dry-Run失败: failed_count={len(dom_failed_locators)}, "
                                f"failed={failed_preview}"
                            )
                            logger.info(f"   ❌ [DomCache] {reason}")
                            try:
                                _record_cache_failure(
                                    "domcache", dom_cache_hit.id,
                                    _extract_domain_key_from_url(current_url),
                                    reason
                                )
                            except Exception as dom_mark_exc:
                                logger.info(
                                    f"   ⚠️ [DomCache] Dry-Run失败打标异常: {dom_mark_exc}")

                            if dom_cache_hit.id and dom_cache_hit.id not in failed_dom_cache_ids:
                                failed_dom_cache_ids.append(dom_cache_hit.id)
                            dom_cache_hit = None
            except Exception as e:
                logger.info(f"   ⚠️ [DomCache] 检索异常: {e}")

        if should_analyze:
            if dom_cache_hit and dom_cache_hit.locator_suggestions:
                page_context = finished_steps[-1] if finished_steps else "初始页面"
                new_strategy_entry = {
                    "page_context": page_context,
                    "url": current_url,
                    "strategies": dom_cache_hit.locator_suggestions,
                }
                logger.info("   ⏭️ [Observer] DomCache 命中，跳过视觉定位分析")
            else:
                if has_failure and current_dom_hash == previous_dom_hash:
                    logger.info(f"   🔄 [Observer] 检测到失败记录，强制重新分析 DOM...")
                logger.info(
                    f"   -> 正在进行视觉定位分析 (Context: {len(finished_steps)} finished steps)...")
                from config import (
                    OBSERVER_DRY_RUN_ENABLED,
                    OBSERVER_DRY_RUN_FAIL_RATIO_THRESHOLD,
                    OBSERVER_DRY_RUN_TIMEOUT_SECONDS,
                )

                previous_failures = list(reflections or [])
                observer_requirement = task
                if verification_failed:
                    focus_text = _verification_focus_text(verification)
                    observer_requirement = (
                        f"{task}\n\n"
                        f"【本轮仅修复失败点】\n"
                        f"{focus_text}\n"
                        f"要求：优先修复 failed_locator/failed_action，禁止全局重写。"
                    )
                    previous_failures.append(
                        f"Verifier失败摘要: {verification.get('summary', '')}")

                base_observer_requirement = observer_requirement
                max_attempts = 1
                if OBSERVER_DRY_RUN_ENABLED:
                    # 约束：每轮仅做一次全量 Dry-Run，失败后直接回到 Observer
                    max_attempts = 1

                locator_suggestions = []
                dry_run_feedback_notes = []

                for attempt_idx in range(max_attempts):
                    attempt_requirement = base_observer_requirement
                    if dry_run_feedback_notes and observer_dry_run_failed_locators:
                        failed_list_for_prompt = "\n".join(
                            [f"- {x}" for x in observer_dry_run_failed_locators[:30]]
                        )
                        attempt_requirement = (
                            f"{base_observer_requirement}\n\n"
                            f"【Observer Dry-Run 失败反馈】\n"
                            f"以下定位在上一轮 Dry-Run 中失败，请逐项修复：\n"
                            f"{failed_list_for_prompt}\n"
                            f"要求：仅修复失败定位器，禁止全局重写。"
                        )

                    analyzed = observer.analyze_locator_strategy(
                        dom,
                        attempt_requirement,
                        current_url,
                        previous_steps=finished_steps,
                        ignore_cache=has_failure or attempt_idx > 0,
                        previous_failures=previous_failures + dry_run_feedback_notes,
                    )

                    if isinstance(analyzed, dict):
                        analyzed = [analyzed]
                    elif not isinstance(analyzed, list):
                        analyzed = []

                    locator_suggestions = analyzed

                    if not OBSERVER_DRY_RUN_ENABLED:
                        break

                    observer_dry_run_started_at = time.time()
                    logger.info(
                        f"   🔎 [Observer] Dry-Run开始: round={attempt_idx + 1}/{max_attempts}, "
                        f"strategies={len(locator_suggestions)}"
                    )
                    dry_ok, failed_locators, validated_count = _dry_run_observer_strategies(
                        config=config,
                        strategies=locator_suggestions,
                        timeout_seconds=OBSERVER_DRY_RUN_TIMEOUT_SECONDS,
                    )
                    observer_dry_run_elapsed = time.time() - observer_dry_run_started_at

                    # L3: 基于失败率的阈值判断
                    fail_ratio = (
                        len(failed_locators) / validated_count
                        if validated_count > 0 else 1.0
                    )
                    logger.info(
                        f"   📊 [Observer] Dry-Run统计: "
                        f"validated={validated_count}, failed={len(failed_locators)}, "
                        f"fail_ratio={fail_ratio:.0%}, threshold={OBSERVER_DRY_RUN_FAIL_RATIO_THRESHOLD:.0%}, "
                        f"elapsed={observer_dry_run_elapsed:.2f}s"
                    )

                    if dry_ok or fail_ratio < OBSERVER_DRY_RUN_FAIL_RATIO_THRESHOLD:
                        logger.info(
                            f"   ✅ [Observer] Dry-Run{'通过' if dry_ok else '放行(低于阈值)'}: "
                            f"round={attempt_idx + 1}/{max_attempts}, "
                            f"validated={validated_count}, "
                            f"elapsed={observer_dry_run_elapsed:.2f}s"
                        )
                        observer_dry_run_last_locator = ""
                        observer_dry_run_last_note = ""
                        observer_dry_run_failed_locators = []
                        break

                    observer_dry_run_failed_locators = list(
                        failed_locators or [])
                    observer_dry_run_last_locator = (
                        observer_dry_run_failed_locators[0]
                        if observer_dry_run_failed_locators else "无可校验locator"
                    )
                    observer_dry_run_last_note = (
                        f"Observer Dry-Run失败(第 {attempt_idx + 1}/{max_attempts} 轮): "
                        f"failed_count={len(observer_dry_run_failed_locators)}"
                    )
                    failed_preview = "; ".join(
                        observer_dry_run_failed_locators[:20])
                    dry_run_feedback_notes.append(
                        f"{observer_dry_run_last_note}; failed={failed_preview}"
                    )
                    logger.info(
                        f"   ❌ [Observer] {observer_dry_run_last_note}")
                    logger.info(
                        "   📊 [ObserverDryRunStat] "
                        f"consecutive_failures={len(dry_run_feedback_notes)}, "
                        f"max_attempts={max_attempts}, "
                        f"failed_count={len(observer_dry_run_failed_locators)}, "
                        f"first_failed_locator={observer_dry_run_last_locator or '(未命中定位器)'}"
                    )

                    if attempt_idx == max_attempts - 1:
                        observer_dry_run_exhausted = True
                        locator_suggestions = []
                        failed_summary = "; ".join(
                            observer_dry_run_failed_locators[:10])
                        logger.info(
                            "   ⚠️ [Observer] Dry-Run 重试耗尽，清空本轮失效定位策略，避免误导 Coder")
                        logger.info(
                            "   📊 [ObserverDryRunSummary] "
                            f"consecutive_failures={len(dry_run_feedback_notes)}, "
                            f"failed_count={len(observer_dry_run_failed_locators)}, "
                            f"failed_locators={failed_summary}, "
                            f"url={current_url}"
                        )

                if locator_suggestions:
                    page_context = finished_steps[-1] if finished_steps else "初始页面"
                    new_strategy_entry = {
                        "page_context": page_context,
                        "url": current_url,
                        "strategies": locator_suggestions
                    }
                    logger.info(f"   -> 新增策略条目: {page_context[:30]}...")
                else:
                    logger.info("   ⏭️ [Observer] 本轮未产出可用策略，跳过策略写入")
        else:
            logger.info("   -> 页面无变化，复用历史策略 (Skipping Observer Analysis)...")

        # 合并基础更新
        update_dict = {
            **base_update,
            "dom_skeleton": dom,
            "dom_hash": current_dom_hash,
            "current_url": current_url,
            "locator_suggestions": [new_strategy_entry] if new_strategy_entry else [],
            "_observer_source": "dom_cache" if dom_cache_hit else "observer",
            "_dom_cache_hit_id": dom_cache_hit.id if dom_cache_hit else None,
            "_failed_dom_cache_ids": failed_dom_cache_ids,
        }

        if observer_dry_run_exhausted:
            failed_text = "\n".join(
                [f"- {x}" for x in observer_dry_run_failed_locators[:30]]
            ) or "- (无)"
            failed_summary = " | ".join(
                observer_dry_run_failed_locators[:10])
            update_dict["messages"] = [
                AIMessage(content=(
                    f"【Observer Dry-Run失败】{observer_dry_run_last_note}\n"
                    f"失败定位如下：\n{failed_text}\n"
                    "已回到 Observer 重新定位。"
                ))
            ]
            update_dict["reflections"] = [
                f"{observer_dry_run_last_note}; failed={failed_summary}"
            ]
            update_dict["verification_result"] = _build_verification_result(
                is_success=False,
                is_done=False,
                summary="Observer 生成定位器 Dry-Run 连续失败",
                source="manual",
                failure_scope="local",
                failed_action=task,
                failed_locator=failed_summary or observer_dry_run_last_locator,
                evidence=f"{observer_dry_run_last_note}\n{failed_text}",
                fix_hint="Observer 仅修复 failed_locator，禁止全局改写后重试",
            )

        # 如果刚做完重新分析（因为失败触发），清空错误标记
        if has_failure and should_analyze and not observer_dry_run_exhausted:
            update_dict["reflections"] = []  # 清空旧的反思
            update_dict["error_type"] = None

        return Command(update=update_dict, goto="Observer" if observer_dry_run_exhausted else "Planner")

    except Exception as e:
        logger.info(f"   ⚠️ 环境感知失败: {e}")
        base_update["dom_skeleton"] = f"DOM Capture Failed: {e}"
        base_update["current_url"] = current_url
        return Command(update=base_update, goto="Planner")


# =============================================================================
# RAG Node - 向量数据库操作调度节点