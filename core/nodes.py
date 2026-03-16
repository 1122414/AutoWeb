import json
import time
import hashlib
import re
import traceback
from datetime import datetime
import tiktoken
from typing import Any, Dict, Literal, Optional, Union
from urllib.parse import urlparse
from langchain_core.messages import HumanMessage, AIMessage, RemoveMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from core.state_v2 import AgentState
from skills.actor import BrowserActor
from skills.logger import logger
from prompts.coder_prompts import ACTION_CODE_GEN_PROMPT, CODER_TASK_WRAPPER
from prompts.planner_prompts import PLANNER_START_PROMPT, PLANNER_STEP_PROMPT, PLANNER_CONTINUE_PROMPT, PLANNER_FORCE_SKIP_PROMPT
from prompts.verifier_prompts import VERIFIER_CHECK_PROMPT, ERROR_RECOVERY_PROMPT
from config import RAG_STORE_KEYWORDS, RAG_QA_KEYWORDS, RAG_GOAL_KEYWORDS, RAG_DONE_KEYWORDS, CONTINUE_KEYWORDS

# ====== 依赖注入辅助函数 ======


def _get_tab(config: RunnableConfig):
    """从 config 获取浏览器标签页"""
    browser = config["configurable"].get("browser")
    return browser.latest_tab if browser else None


def _parse_iso_datetime(text: str) -> Optional[datetime]:
    value = (text or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value, fmt)
            except Exception:
                continue
    return None


def _is_hit_from_current_task(created_at: str, task_started_at: Optional[datetime]) -> bool:
    if task_started_at is None:
        return False
    created_dt = _parse_iso_datetime(created_at)
    if created_dt is None:
        return False
    return created_dt >= task_started_at


def _detect_task_continuity(new_task: str, current_url: str, old_task: str = "") -> bool:
    """
    [任务连续性检测] 判断新任务是否是旧任务的延续

    返回:
    - True: 延续任务（保留旧状态）
    - False: 全新任务（清空旧状态）

    判断逻辑:
    1. 快速关键词匹配: 包含"继续"/"接着"/"下一页"等词 → 延续
    2. URL 域名匹配: 新任务中明确提到的 URL 与当前 URL 同域 → 延续
    3. 默认: 全新任务
    """

    # 1. 延续关键词检测（关键词定义在 config.py）
    for kw in CONTINUE_KEYWORDS:
        if kw in new_task:
            logger.info(f"   🔗 [TaskContinuity] 检测到延续关键词: '{kw}' → 保留旧状态")
            return True

    # 2. URL 域名匹配
    if current_url:
        try:
            current_domain = urlparse(current_url).netloc
            # 检查新任务是否提到当前域名
            if current_domain and current_domain in new_task:
                logger.info(
                    f"   🔗 [TaskContinuity] 任务中包含当前域名 '{current_domain}' → 保留旧状态")
                return True

            # 检查新任务是否提到其他 URL（全新任务标志）
            urls_in_task = re.findall(r'https?://[^\s<>"\']+', new_task)
            for url in urls_in_task:
                task_domain = urlparse(url).netloc
                if task_domain and task_domain != current_domain:
                    logger.info(
                        f"   🆕 [TaskContinuity] 任务指向新域名 '{task_domain}' (当前: '{current_domain}') → 全新任务")
                    return False
        except Exception as e:
            logger.info(f"   ⚠️ [TaskContinuity] URL 解析失败: {e}")

    # 3. 默认: 全新任务（保守策略，避免旧状态污染）
    logger.info(f"   🆕 [TaskContinuity] 无明确延续标志 → 视为全新任务，清空旧状态")
    return False


# ==============================================================================
# Locator 摘要提取（用于 CodeCache embedding）
# ==============================================================================
def _extract_locator_info(state: dict) -> str:
    """从 state 的 locator_suggestions 中提取 locator 摘要字符串"""
    suggestions = state.get("locator_suggestions", [])
    if not suggestions:
        return ""
    parts = []
    for entry in suggestions:
        strategies = entry.get("strategies", [])
        if isinstance(strategies, list):
            for s in strategies:
                if isinstance(s, dict):
                    loc = s.get("locator", "")
                    reason = s.get("reason", "")
                    if loc:
                        parts.append(f"{loc} ({reason})" if reason else loc)
        elif isinstance(strategies, dict):
            loc = strategies.get("locator", "")
            if loc:
                parts.append(loc)
    return " | ".join(parts) if parts else ""


def _extract_domain_key_from_url(url: str) -> str:
    try:
        parsed = urlparse(url or "")
        host = (parsed.hostname or "").strip().lower()
        if not host:
            return ""
        try:
            import tldextract

            extractor = tldextract.TLDExtract(suffix_list_urls=None)
            ext = extractor(host)
            if ext.domain and ext.suffix:
                return f"{ext.domain}.{ext.suffix}"[:255]
        except Exception:
            pass
        parts = [x for x in host.split(".") if x]
        if len(parts) >= 2:
            return ".".join(parts[-2:])[:255]
        return host[:255]
    except Exception:
        return ""


def _build_step_context(finished_steps: list) -> str:
    from config import DOM_CACHE_STEP_WINDOW, DOM_CACHE_STEP_TEXT_MAX

    steps = finished_steps or []
    window = max(1, int(DOM_CACHE_STEP_WINDOW))
    last_steps = steps[-window:] if steps else []
    text = " | ".join([str(x).strip() for x in last_steps if str(x).strip()])
    return text[:max(100, int(DOM_CACHE_STEP_TEXT_MAX))]


def _extract_locator_candidates(locator_info: str, code: str) -> list:
    candidates = []

    info = str(locator_info or "").strip()
    if info:
        for part in info.split("|"):
            item = part.strip()
            if not item:
                continue
            loc = item.split("(", 1)[0].strip()
            if loc:
                candidates.append(loc)

    code_text = code or ""
    pattern = re.compile(r"(?:tab|new_tab|page)\.ele\(\s*(['\"])(.+?)\1")
    for _, locator in pattern.findall(code_text):
        loc = (locator or "").strip()
        if loc:
            candidates.append(loc)

    seen = set()
    dedup = []
    for loc in candidates:
        if loc in seen:
            continue
        seen.add(loc)
        dedup.append(loc)
    return dedup


def _extract_locators_from_strategies(strategies: Any) -> list:
    locators = []
    if isinstance(strategies, dict):
        strategies = [strategies]
    if not isinstance(strategies, list):
        return locators

    for item in strategies:
        if not isinstance(item, dict):
            continue
        loc = str(item.get("locator", "")).strip()
        if loc:
            locators.append(loc)
        sub = item.get("sub_locators", {})
        if isinstance(sub, dict):
            for value in sub.values():
                if isinstance(value, str) and value.strip():
                    locators.append(value.strip())

    seen = set()
    dedup = []
    for loc in locators:
        if loc in seen:
            continue
        seen.add(loc)
        dedup.append(loc)
    return dedup


def _normalize_locator_token(locator: str) -> str:
    text = str(locator or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    return text


def _has_locator_overlap(failed_locator: str, candidates: list) -> bool:
    failed = _normalize_locator_token(failed_locator)
    if not failed:
        return False
    for item in candidates or []:
        token = _normalize_locator_token(item)
        if not token:
            continue
        if failed == token or failed in token or token in failed:
            return True
    return False


def _probe_locator_with_polling(
    tab,
    locator: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
    require_visible: bool,
) -> bool:
    deadline = time.time() + max(0.1, float(timeout_seconds))
    interval = max(0.05, float(poll_interval_seconds))

    while time.time() < deadline:
        try:
            ele = tab.ele(locator, timeout=0)
        except TypeError:
            try:
                ele = tab.ele(locator)
            except Exception:
                ele = None
        except Exception:
            ele = None

        if ele:
            if not require_visible:
                return True
            try:
                if ele.states.is_displayed:
                    return True
            except Exception:
                return True

        time.sleep(interval)
    return False


def _dry_run_cache_hit_locators(
    config: RunnableConfig,
    locator_candidates: list,
    timeout_seconds: float,
    poll_interval_seconds: float,
    require_visible: bool,
) -> tuple[bool, str]:
    tab = _get_tab(config)
    if tab is None:
        return False, "无可用浏览器标签页"
    if not locator_candidates:
        return True, ""

    for loc in locator_candidates:
        ok = _probe_locator_with_polling(
            tab=tab,
            locator=loc,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            require_visible=require_visible,
        )
        if ok:
            return True, ""
    return False, locator_candidates[0]


def _normalize_failure_scope(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "global" if text == "global" else "local"


def _normalize_verification_source(value: Any, default: str = "verifier") -> str:
    valid = {"verifier", "executor", "error_handler", "manual"}
    text = str(value or default).strip().lower()
    return text if text in valid else default


def _build_verification_result(
    *,
    is_success: bool,
    summary: str,
    source: str,
    is_done: bool = False,
    failure_scope: str = "local",
    failed_action: str = "",
    failed_locator: str = "",
    evidence: str = "",
    fix_hint: str = "",
) -> Dict[str, Any]:
    success = bool(is_success)
    return {
        "is_success": success,
        "is_done": bool(is_done) if success else False,
        "summary": str(summary or "Step executed.").strip(),
        "source": _normalize_verification_source(source),
        "failure_scope": _normalize_failure_scope(failure_scope),
        "failed_action": str(failed_action or "").strip(),
        "failed_locator": str(failed_locator or "").strip(),
        "evidence": str(evidence or "").strip(),
        "fix_hint": str(fix_hint or "").strip(),
    }


def _coerce_verification_result(
    verification: Optional[Dict[str, Any]],
    *,
    fallback_is_success: bool = False,
    fallback_summary: str = "Step executed.",
    fallback_source: str = "verifier",
    fallback_is_done: bool = False,
    fallback_failure_scope: str = "local",
    fallback_failed_action: str = "",
    fallback_failed_locator: str = "",
    fallback_evidence: str = "",
    fallback_fix_hint: str = "",
) -> Dict[str, Any]:
    payload = verification or {}
    return _build_verification_result(
        is_success=bool(payload.get("is_success", fallback_is_success)),
        is_done=bool(payload.get("is_done", fallback_is_done)),
        summary=str(payload.get("summary", fallback_summary)),
        source=str(payload.get("source", fallback_source)),
        failure_scope=str(payload.get(
            "failure_scope", fallback_failure_scope)),
        failed_action=str(payload.get(
            "failed_action", fallback_failed_action)),
        failed_locator=str(payload.get(
            "failed_locator", fallback_failed_locator)),
        evidence=str(payload.get("evidence", fallback_evidence)),
        fix_hint=str(payload.get("fix_hint", fallback_fix_hint)),
    )


def _is_failed_verification(verification: Optional[Dict[str, Any]]) -> bool:
    return bool(verification) and bool(verification.get("is_success", True)) is False


def _parse_verifier_result_content(content: str) -> Dict[str, Any]:
    summary = "Step executed."
    failure_scope = "local"
    failed_action = ""
    failed_locator = ""
    evidence = ""
    fix_hint = ""
    is_success = "Status: STEP_SUCCESS" in content

    for raw_line in (content or "").split("\n"):
        line = raw_line.strip()
        line_lower = line.lower()
        if line.startswith("Summary:"):
            summary = line.replace("Summary:", "", 1).strip() or summary
        elif line_lower.startswith("failurescope:"):
            failure_scope = line.split(
                ":", 1)[1].strip() if ":" in line else failure_scope
        elif line_lower.startswith("failedaction:"):
            failed_action = line.split(
                ":", 1)[1].strip() if ":" in line else failed_action
        elif line_lower.startswith("failedlocator:"):
            failed_locator = line.split(
                ":", 1)[1].strip() if ":" in line else failed_locator
        elif line_lower.startswith("evidence:"):
            evidence = line.split(
                ":", 1)[1].strip() if ":" in line else evidence
        elif line_lower.startswith("fixhint:"):
            fix_hint = line.split(
                ":", 1)[1].strip() if ":" in line else fix_hint

    return {
        "is_success": is_success,
        "summary": summary,
        "failure_scope": _normalize_failure_scope(failure_scope),
        "failed_action": failed_action,
        "failed_locator": failed_locator,
        "evidence": evidence,
        "fix_hint": fix_hint,
    }


def _verification_focus_text(verification: Optional[Dict[str, Any]]) -> str:
    if not _is_failed_verification(verification):
        return "(无)"
    v = verification or {}
    scope = _normalize_failure_scope(v.get("failure_scope", "local"))
    action = str(v.get("failed_action", "")).strip() or "(未提供)"
    locator = str(v.get("failed_locator", "")).strip() or "(未提供)"
    evidence = str(v.get("evidence", "")).strip() or str(
        v.get("summary", "")).strip() or "(未提供)"
    fix_hint = str(v.get("fix_hint", "")).strip() or "(未提供)"
    return (
        f"- failure_scope: {scope}\n"
        f"- failed_action: {action}\n"
        f"- failed_locator: {locator}\n"
        f"- evidence: {evidence}\n"
        f"- fix_hint: {fix_hint}"
    )


def _looks_like_global_rewrite_plan(plan_text: str) -> bool:
    text = (plan_text or "").lower()
    keywords = ["全局", "全部重写", "从头", "重做", "重写", "推翻", "重新执行全部", "重来"]
    return any(kw in text for kw in keywords)


# ==============================================================================
# 上下文裁剪辅助函数 (tiktoken 水位监控 + 分级裁剪)
# ==============================================================================

def _count_tokens(text: str) -> int:
    """用 tiktoken 计算文本 Token 数（cl100k_base 编码，兼容绝大多数模型）"""
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 2


def _get_summarizer_llm():
    """获取摘要压缩用的独立小模型实例"""
    from langchain_openai import ChatOpenAI
    from config import SUMMARIZER_MODEL_NAME, SUMMARIZER_API_KEY, SUMMARIZER_BASE_URL
    return ChatOpenAI(
        model=SUMMARIZER_MODEL_NAME,
        api_key=SUMMARIZER_API_KEY,
        base_url=SUMMARIZER_BASE_URL,
        temperature=0,
        max_tokens=512,
    )


def _prune_locator_suggestions(accumulated_strategies: list) -> list:
    """
    保留最近 N 组页面的定位策略。

    策略：直接保留最后出现的 N 组，不再按 URL 强制去重覆盖，
    避免同一个页面后续不同操作的策略互相覆盖。
    """
    from config import CONTEXT_MAX_UNIQUE_PAGES

    if len(accumulated_strategies) <= CONTEXT_MAX_UNIQUE_PAGES:
        return accumulated_strategies

    pruned = accumulated_strategies[-CONTEXT_MAX_UNIQUE_PAGES:]

    logger.info(
        f"   ✂️ [Context] locator_suggestions 裁剪: "
        f"{len(accumulated_strategies)} → 保留最近 {len(pruned)} 组"
    )

    return pruned


def _prune_finished_steps(finished_steps: list, prompt_text: str) -> str:
    """
    tiktoken 水位监控触发的 finished_steps 滚动摘要。

    逻辑：
    1. 先构建完整的 finished_steps_str
    2. 用 tiktoken 计算整个 prompt 的 Token 数
    3. 如果超过阈值，用独立小模型将早期步骤压缩为摘要
    """
    from config import (PLANNER_CONTEXT_WINDOW, CONTEXT_PRUNE_RATIO,
                        CONTEXT_RECENT_KEEP)

    finished_steps_str = "\n".join(
        [f"- {s}" for s in finished_steps]) if finished_steps else "(无)"

    threshold = int(PLANNER_CONTEXT_WINDOW * CONTEXT_PRUNE_RATIO)
    current_tokens = _count_tokens(prompt_text)

    logger.info(
        f"   📊 [Context] Token 水位: {current_tokens}/{threshold} "
        f"({current_tokens * 100 // max(threshold, 1)}%)"
    )

    if current_tokens <= threshold:
        return finished_steps_str

    # 超阈值 → 用独立小模型压缩早期步骤
    if not finished_steps or len(finished_steps) <= CONTEXT_RECENT_KEEP:
        return finished_steps_str

    logger.info(
        f"   ✂️ [Context] 第二级裁剪: finished_steps 滚动摘要 "
        f"(保留最近 {CONTEXT_RECENT_KEEP} 条, 压缩前 {len(finished_steps) - CONTEXT_RECENT_KEEP} 条)"
    )

    early = finished_steps[:-CONTEXT_RECENT_KEEP]
    recent = finished_steps[-CONTEXT_RECENT_KEEP:]

    try:
        summarizer = _get_summarizer_llm()
        summary_prompt = (
            "请用1-2句话总结以下已完成的操作步骤，"
            "保留关键信息（如爬取了哪些数据、到了第几页等）：\n"
            + "\n".join([f"- {s}" for s in early])
        )
        resp = summarizer.invoke([HumanMessage(content=summary_prompt)])
        early_summary = resp.content.strip()
    except Exception as e:
        logger.warning(f"   ⚠️ [Context] 摘要压缩失败: {e}，使用截断兜底")
        early_summary = f"(已完成 {len(early)} 个早期步骤)"

    recent_str = "\n".join([f"- {s}" for s in recent])
    result = f"[早期摘要] {early_summary}\n[最近步骤]\n{recent_str}"

    new_tokens = _count_tokens(prompt_text.replace(finished_steps_str, result))
    logger.info(f"   ✅ [Context] 裁剪后 Token: {new_tokens}/{threshold}")

    return result


# ==============================================================================
# 代码缓存检索节点
# ==============================================================================
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
        CODE_CACHE_DRY_RUN_POLL_INTERVAL_SECONDS,
        CODE_CACHE_DRY_RUN_REQUIRE_VISIBLE,
        CODE_CACHE_DRY_RUN_TIMEOUT_SECONDS,
        CODE_CACHE_ENABLED,
        CODE_CACHE_THRESHOLD,
    )

    # 检查本轮是否已有缓存失败（防止死循环）
    if state.get("_cache_failed_this_round"):
        logger.info("⚠️ [CacheLookup] 本轮缓存已失败，强制跳过")
        return Command(
            update={"_code_source": "llm"},
            goto="Coder"
        )

    # 检查是否启用缓存
    if not CODE_CACHE_ENABLED:
        logger.info("⏭️ [CacheLookup] 代码缓存已禁用，跳过检索")
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
        eligible_hits = [
            hit for hit in hits
            if (not hit.id) or (hit.id not in failed_code_cache_ids)
        ]

        if hits and len(eligible_hits) < len(hits):
            logger.info(
                f"   ⏭️ [CacheLookup] 过滤失败缓存命中: {len(hits) - len(eligible_hits)} 条")

        if task_started_at is not None:
            before_len = len(eligible_hits)
            eligible_hits = [
                hit for hit in eligible_hits
                if not _is_hit_from_current_task(hit.created_at, task_started_at)
            ]
            if len(eligible_hits) < before_len:
                logger.info(
                    f"   ⏭️ [CacheLookup] 过滤同任务新写入缓存: {before_len - len(eligible_hits)} 条")

        if eligible_hits and eligible_hits[0].score >= CODE_CACHE_THRESHOLD:
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
                dry_ok, failed_locator = _dry_run_cache_hit_locators(
                    config=config,
                    locator_candidates=loc_candidates,
                    timeout_seconds=CODE_CACHE_DRY_RUN_TIMEOUT_SECONDS,
                    poll_interval_seconds=CODE_CACHE_DRY_RUN_POLL_INTERVAL_SECONDS,
                    require_visible=CODE_CACHE_DRY_RUN_REQUIRE_VISIBLE,
                )
                if not dry_ok:
                    reason = f"Dry-Run失败: locator={failed_locator}"
                    logger.info(f"   ❌ [CacheLookup] {reason}")
                    failed_cache_ids = list(state.get("_failed_code_cache_ids", []) or [])
                    failed_dom_cache_ids = list(state.get("_failed_dom_cache_ids", []) or [])
                    if best_hit.id and best_hit.id not in failed_cache_ids:
                        failed_cache_ids.append(best_hit.id)

                    observer_source = state.get("_observer_source", "")
                    dom_cache_hit_id = state.get("_dom_cache_hit_id", "")
                    dom_dual_invalidate = False

                    if observer_source == "dom_cache" and dom_cache_hit_id:
                        strategies = []
                        loc_entries = state.get("locator_suggestions", []) or []
                        if loc_entries and isinstance(loc_entries[-1], dict):
                            strategies = loc_entries[-1].get("strategies", [])
                        dom_locators = _extract_locators_from_strategies(strategies)
                        dom_dual_invalidate = _has_locator_overlap(
                            failed_locator,
                            dom_locators,
                        )

                    try:
                        from skills.code_cache import code_cache_manager
                        from skills.dom_cache import dom_cache_manager
                        from skills.cache_blacklist import cache_soft_blacklist

                        if best_hit.id:
                            code_cache_manager.record_failure(best_hit.id, reason=reason)
                            cache_soft_blacklist.mark_failed(
                                cache_type="codecache",
                                domain_key=_extract_domain_key_from_url(current_url),
                                cache_id=best_hit.id,
                                reason=reason,
                            )

                        if dom_dual_invalidate and dom_cache_hit_id:
                            dom_reason = f"Code Dry-Run联动失效: locator={failed_locator}"
                            dom_cache_manager.record_failure(
                                dom_cache_hit_id,
                                reason=dom_reason,
                            )
                            cache_soft_blacklist.mark_failed(
                                cache_type="domcache",
                                domain_key=_extract_domain_key_from_url(current_url),
                                cache_id=dom_cache_hit_id,
                                reason=dom_reason,
                            )
                            if dom_cache_hit_id not in failed_dom_cache_ids:
                                failed_dom_cache_ids.append(dom_cache_hit_id)
                    except Exception as mark_exc:
                        logger.info(f"   ⚠️ [CacheLookup] Dry-Run 失败打标异常: {mark_exc}")

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


def _handle_cache_failure(state: AgentState, updates: dict) -> Command:
    """缓存代码失败统一处理：记录失败 + 标记熔断 + 跳 Planner

    调用方负责构建 updates 中的 messages / reflections 等字段，
    本函数只负责：记录失败 + 追加熔断标记。
    """
    cache_hit_id = state.get("_cache_hit_id", "")
    failed_cache_ids = list(state.get("_failed_code_cache_ids", []) or [])
    if cache_hit_id:
        try:
            from skills.code_cache import code_cache_manager
            from skills.cache_blacklist import cache_soft_blacklist
            code_cache_manager.record_failure(cache_hit_id, reason="执行/验收失败")
            cache_soft_blacklist.mark_failed(
                cache_type="codecache",
                domain_key=_extract_domain_key_from_url(state.get("current_url", "")),
                cache_id=cache_hit_id,
                reason="执行/验收失败",
            )
        except Exception as e:
            logger.info(f"   ⚠️ [CodeCache] 记录失败异常: {e}")
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
            failure_scope="local",
            failed_action=plan,
            evidence=error_msg,
            fix_hint="回到 Observer 重新感知并修复失败点",
        )
        return Command(update=updates, goto="Observer")


def observer_node(state: AgentState, config: RunnableConfig, observer) -> Command[Literal["Planner"]]:
    """[Observer] 环境感知节点：捕获 DOM 并生成定位策略"""
    logger.info("\n👁️ [Observer] 正在感知环境...")

    # 新一轮开始，重置缓存失败标记
    base_update = {
        "_cache_failed_this_round": False,
        "_observer_source": None,
        "_dom_cache_hit_id": None,
    }

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

        # DOM Cache: 如果上轮是 DomCache 命中且后续失败，记录失败（不删除，供用户审查）
        observer_source = state.get("_observer_source", "")
        dom_cache_hit_id = state.get("_dom_cache_hit_id", "")
        if has_failure and observer_source == "dom_cache" and dom_cache_hit_id:
            try:
                from config import DOM_CACHE_ENABLED
                if DOM_CACHE_ENABLED:
                    from skills.dom_cache import dom_cache_manager
                    from skills.cache_blacklist import cache_soft_blacklist
                    dom_cache_manager.record_failure(
                        dom_cache_hit_id, reason="后续执行失败")
                    cache_soft_blacklist.mark_failed(
                        cache_type="domcache",
                        domain_key=_extract_domain_key_from_url(current_url),
                        cache_id=dom_cache_hit_id,
                        reason="后续执行失败",
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
                    DOM_CACHE_DRY_RUN_POLL_INTERVAL_SECONDS,
                    DOM_CACHE_DRY_RUN_REQUIRE_VISIBLE,
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
                        dom_locators = _extract_locators_from_strategies(
                            dom_cache_hit.locator_suggestions)
                        dom_dry_ok, dom_failed_locator = _dry_run_cache_hit_locators(
                            config=config,
                            locator_candidates=dom_locators,
                            timeout_seconds=DOM_CACHE_DRY_RUN_TIMEOUT_SECONDS,
                            poll_interval_seconds=DOM_CACHE_DRY_RUN_POLL_INTERVAL_SECONDS,
                            require_visible=DOM_CACHE_DRY_RUN_REQUIRE_VISIBLE,
                        )
                        if not dom_dry_ok:
                            reason = f"Dom Dry-Run失败: locator={dom_failed_locator}"
                            logger.info(f"   ❌ [DomCache] {reason}")
                            try:
                                from skills.cache_blacklist import cache_soft_blacklist
                                dom_cache_manager.record_failure(
                                    dom_cache_hit.id,
                                    reason=reason,
                                )
                                cache_soft_blacklist.mark_failed(
                                    cache_type="domcache",
                                    domain_key=_extract_domain_key_from_url(current_url),
                                    cache_id=dom_cache_hit.id,
                                    reason=reason,
                                )
                            except Exception as dom_mark_exc:
                                logger.info(f"   ⚠️ [DomCache] Dry-Run失败打标异常: {dom_mark_exc}")

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
                locator_suggestions = observer.analyze_locator_strategy(
                    dom,
                    observer_requirement,
                    current_url,
                    previous_steps=finished_steps,
                    ignore_cache=has_failure,
                    previous_failures=previous_failures,
                )

                if isinstance(locator_suggestions, dict):
                    locator_suggestions = [locator_suggestions]

                page_context = finished_steps[-1] if finished_steps else "初始页面"
                new_strategy_entry = {
                    "page_context": page_context,
                    "url": current_url,
                    "strategies": locator_suggestions
                }
                logger.info(f"   -> 新增策略条目: {page_context[:30]}...")
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

        # 如果刚做完重新分析（因为失败触发），清空错误标记
        if has_failure and should_analyze:
            update_dict["reflections"] = []  # 清空旧的反思
            update_dict["error_type"] = None

        return Command(update=update_dict, goto="Planner")

    except Exception as e:
        logger.info(f"   ⚠️ 环境感知失败: {e}")
        base_update["dom_skeleton"] = f"DOM Capture Failed: {e}"
        base_update["current_url"] = current_url
        return Command(update=base_update, goto="Planner")


# =============================================================================
# RAG Node - 向量数据库操作调度节点
# =============================================================================

def rag_node(state: AgentState, config: RunnableConfig) -> Command[Literal["Observer"]]:
    """
    [RAG Node] 统一处理所有向量数据库操作

    设计说明:
        rag_task_type 由上游节点（Planner / Verifier）写入 State，
        RAGNode 读取后分派。这是 LangGraph Command 模式下的惯用法——
        Command(goto=...) 只能指定目标节点，无法传递额外参数，
        因此必须通过 State 携带路由上下文。

    任务类型:
    - store_kb: 读取最新 JSON → 存入知识库
    - store_cache: 将验证通过的代码存入 Code Cache 和 Dom Cache
    - qa: 查询知识库并返回答案
    """
    rag_task = state.get("rag_task_type")
    logger.info(f"\n📚 [RAG Node] 任务类型: {rag_task}")

    result_summary = ""

    try:
        if rag_task == "store_kb":
            result_summary = _rag_store_kb(state)

        elif rag_task == "store_cache":
            result_summary = _rag_store_cache(state, config)

        elif rag_task == "qa":
            result_summary = _rag_qa(state)

        else:
            result_summary = f"未知的 RAG 任务类型: {rag_task}"
            logger.warning(f"   ⚠️ {result_summary}")

    except Exception as e:
        result_summary = f"RAG 执行失败: {e}"
        logger.error(f"   ❌ {result_summary}")

    logger.info(f"   📋 RAG 结果: {result_summary[:100]}")

    return Command(
        update={
            "messages": [AIMessage(content=f"[RAG] {result_summary}")],
            "rag_task_type": None,  # 清空任务标记
            "finished_steps": [result_summary] if rag_task != "store_cache" else [],
        },
        goto="Observer"
    )


def _rag_store_kb(state: AgentState) -> str:
    """[RAG] 将最新输出数据存入知识库（支持 JSON / CSV / SQLite）"""
    import glob
    import os
    import csv
    import sqlite3

    # 1. 查找 output 目录下最新的数据文件（支持域名子目录）
    files = glob.glob("output/**/*.json", recursive=True) + \
        glob.glob("output/**/*.csv", recursive=True) + \
        glob.glob("output/**/*.jsonl", recursive=True)

    # 同时检查 SQLite 数据库
    db_files = glob.glob("*.db") + glob.glob("output/*.db")

    all_sources = files + db_files
    if not all_sources:
        return "未找到任何数据文件（output/*.json, *.csv, *.db）"

    latest_file = max(all_sources, key=os.path.getmtime)
    ext = os.path.splitext(latest_file)[1].lower()
    logger.info(f"   📂 最新数据文件: {latest_file} (格式: {ext})")

    data = []

    # 2. 根据格式读取数据
    if ext == ".json":
        with open(latest_file, encoding="utf-8") as f:
            raw = json.load(f)
            data = raw if isinstance(raw, list) else [raw]

    elif ext == ".jsonl":
        with open(latest_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data.append(json.loads(line))

    elif ext == ".csv":
        with open(latest_file, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            data = [dict(row) for row in reader]

    elif ext == ".db":
        conn = sqlite3.connect(latest_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        # 获取所有用户表
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [row[0] for row in cursor.fetchall()]
        for table in tables:
            cursor.execute(f"SELECT * FROM {table}")
            rows = cursor.fetchall()
            for row in rows:
                data.append(dict(row))
        conn.close()
        logger.info(f"   📊 从 SQLite 读取 {len(tables)} 张表")

    if not data:
        return f"文件 {latest_file} 中无有效数据"

    logger.info(f"   📊 数据条数: {len(data)}")

    # 3. 存入知识库
    from skills.toolbox import save_to_kb, flush_kb

    source = state.get("current_url", "auto_crawl")
    save_to_kb(data, source=source)
    flush_kb()

    return f"成功将 {len(data)} 条数据从 {latest_file} 存入向量知识库 (save_to_kb)"


def _rag_store_cache(state: AgentState, config: RunnableConfig) -> str:
    """[RAG] 将验证通过的代码/策略存入 Code Cache / Dom Cache"""
    current_url = state.get("current_url", "")

    res_code = "跳过"
    res_dom = "跳过"

    # 存 Code Cache
    if state.get("generated_code") and len(state.get("generated_code", "")) >= 50 and state.get("_code_source") != "cache":
        result_code = _save_code_to_cache(state, current_url)
        res_code = result_code.get("false", result_code.get("true", "未知"))

    # 存 Dom Cache
    if state.get("_observer_source") == "observer":
        result_dom = _save_dom_to_cache(state, current_url)
        res_dom = result_dom.get("false", result_dom.get("true", "未知"))

    return f"代码缓存: {res_code}, DOM缓存: {res_dom}"


def _rag_qa(state: AgentState) -> str:
    """[RAG] 查询知识库并返回答案"""
    from skills.tool_rag import ask_knowledge_base

    # 从 plan 中提取问题
    plan = state.get("plan", "")
    # 清理计划格式，提取实际问题
    question = plan.replace("【计划已生成】", "").strip()
    # 去掉行号前缀
    lines = question.split("\n")
    if lines:
        question = lines[0].strip()
        if question.startswith("1."):
            question = question[2:].strip()

    logger.info(f"   🔍 查询: {question}")
    answer = ask_knowledge_base(question)
    return f"知识库问答完成: {answer[:200]}"


def planner_node(state: AgentState, config: RunnableConfig, llm) -> Command[Literal["CacheLookup", "RAGNode", "__end__"]]:
    """[Planner] 负责制定下一步计划（环境感知已由 Observer 完成）"""
    logger.info("\n🧠 [Planner] 正在制定计划...")
    tab = _get_tab(config)

    task = state["user_task"]
    loop_count = state.get("loop_count", 0)
    finished_steps = state.get("finished_steps", [])

    # 循环限制：防止死循环
    MAX_LOOP_COUNT = 20
    if loop_count >= MAX_LOOP_COUNT:
        logger.info(f"   ⚠️ 达到最大循环次数 ({MAX_LOOP_COUNT})，强制结束任务")
        return Command(
            update={
                "messages": [AIMessage(content=f"【系统】达到最大循环次数 {MAX_LOOP_COUNT}，任务强制终止")],
                "is_complete": True
            },
            goto="__end__"
        )

    # 0. 检测当前页面状态，决定使用哪个 Prompt
    current_url = tab.url if tab else ""
    is_blank = not current_url or current_url.startswith(
        ("about:", "data:", "chrome://"))
    is_google_home = "google.com" in current_url and "/search" not in current_url
    is_initial_page = is_blank or is_google_home

    # 0.1 初始启动（空白页/Google首页）
    if loop_count == 0 and is_initial_page:
        logger.info("   ⏩ [Planner] 初始启动，跳过 DOM 分析，直接生成导航计划。")
        prompt = PLANNER_START_PROMPT.format(task=task)
        response = llm.invoke([HumanMessage(content=prompt)])

        return Command(
            update={
                "messages": [response],
                "plan": response.content,
                "dom_skeleton": "(Start Page - Empty)",
                "loop_count": loop_count + 1,
                "is_complete": False
            },
            goto="CacheLookup"
        )

    # 0.2 新任务但在已有页面上（任务连续性检测）
    if loop_count == 0 and not is_initial_page:
        logger.info(f"   🔄 [Planner] 检测到已有页面: {current_url[:50]}...")

        # 任务连续性检测：判断是延续任务还是全新任务
        is_continuation = _detect_task_continuity(task, current_url)

        if is_continuation:
            # 延续任务：保留旧状态
            logger.info(f"   ✅ [Planner] 延续任务，保留历史状态")
            finished_steps_str = "\n".join(
                [f"- {s}" for s in finished_steps]) if finished_steps else "(无历史步骤)"
            prompt = PLANNER_CONTINUE_PROMPT.format(
                task=task,
                current_url=current_url,
                finished_steps_str=finished_steps_str
            )
            response = llm.invoke([HumanMessage(content=prompt)])

            return Command(
                update={
                    "messages": [response],
                    "plan": response.content,
                    "current_url": current_url,
                    # 保留 locator_suggestions, finished_steps 等
                    "loop_count": loop_count + 1,
                    "is_complete": False
                },
                goto="CacheLookup"
            )
        else:
            # 全新任务：清空所有旧状态
            logger.info(f"   🆕 [Planner] 全新任务，清空旧任务的所有状态...")
            prompt = PLANNER_CONTINUE_PROMPT.format(
                task=task,
                current_url=current_url,
                finished_steps_str="(新任务，无历史步骤)"
            )
            response = llm.invoke([HumanMessage(content=prompt)])

            return Command(
                update={
                    "messages": [response],
                    "plan": response.content,
                    "current_url": current_url,
                    # 全新任务：重置所有旧状态（使用 None 触发 clearable_list_reducer 清空）
                    "locator_suggestions": None,    # 清空定位策略
                    "finished_steps": None,         # 清空历史步骤
                    "reflections": None,            # 清空反思记录
                    "generated_code": None,         # 清空生成的代码
                    "execution_log": None,          # 清空执行日志
                    "verification_result": None,    # 清空验收结果
                    "error": None,                  # 清空错误信息
                    "error_type": None,             # 清空错误类型
                    "coder_retry_count": 0,         # 重置重试计数
                    "_code_source": None,           # 清空代码来源
                    "_cache_failed_this_round": False,  # 重置缓存标记
                    "_cache_hit_id": None,          # 清空 CodeCache 命中 ID
                    "_failed_code_cache_ids": [],   # 清空 CodeCache 失败黑名单
                    "_observer_source": None,       # 清空观察来源
                    "_dom_cache_hit_id": None,      # 清空 DomCache 命中 ID
                    "_failed_dom_cache_ids": [],    # 清空 DomCache 失败黑名单
                    "dom_skeleton": "",             # 清空 DOM（Observer 会重新获取）
                    "dom_hash": None,               # 清空 DOM 哈希
                    "loop_count": 1,                # 从 1 开始（因为这是第一次规划）
                    "_step_fail_count": 0,          # 重置连续失败计数
                    "is_complete": False
                },
                goto="CacheLookup"
            )

    # 1. 从 State 读取 Observer 提供的定位策略（不再自己调用 observer）
    accumulated_strategies = state.get("locator_suggestions", [])
    if accumulated_strategies:
        # 裁剪策略：按 URL 去重保留最近 N 个页面
        accumulated_strategies = _prune_locator_suggestions(
            accumulated_strategies)
        suggestions_str = json.dumps(
            accumulated_strategies, ensure_ascii=False, indent=2)
    else:
        suggestions_str = "无特定定位建议，请自行分析 DOM。"

    # 裁剪 reflections：只保留最新的 N 条失败教训
    from config import CONTEXT_MAX_REFLECTIONS
    reflections = state.get("reflections", [])
    if len(reflections) > CONTEXT_MAX_REFLECTIONS:
        reflections = reflections[-CONTEXT_MAX_REFLECTIONS:]

    reflection_str = ""
    if reflections:
        reflection_str = "\n⚠️ **之前的失败教训 (请在规划时重点规避)**:\n" + \
            "\n".join([f"- {r}" for r in reflections])

    verification = state.get("verification_result", {}) or {}
    last_verification = verification.get(
        "summary", "(无)") if verification else "(无)"
    verification_focus = _verification_focus_text(verification)

    # 连续失败保底：跟踪连续步骤失败次数
    step_fail_count = state.get("_step_fail_count", 0)
    if verification:
        is_last_step_fail = verification.get("is_success", True) is False
        if is_last_step_fail:
            step_fail_count += 1
            logger.info(f"   ⚠️ [Planner] 连续失败计数: {step_fail_count}")
        else:
            step_fail_count = 0

    MAX_STEP_FAIL = 2  # 同一步骤最多失败 2 次，之后强制换方案
    fail_override_hint = ""
    if step_fail_count >= MAX_STEP_FAIL:
        fail_override_hint = PLANNER_FORCE_SKIP_PROMPT.format(
            step_fail_count=step_fail_count,
            last_verification=last_verification
        )
        logger.info(f"   🚨 [Planner] 连续失败 {step_fail_count} 次，注入强制跳过指令")

    # 2. tiktoken 水位监控 + finished_steps 滚动摘要
    # 我们先组装试算的 prompt（不包含 finished_steps 的原文），用来计算基础结构大概占多少 Token
    trial_prompt_template = PLANNER_STEP_PROMPT.format(
        task=task,
        current_url=current_url,
        finished_steps_str="{finished_steps_str}",
        suggestions_str=suggestions_str,
        reflection_str=reflection_str,
        last_verification=last_verification,
        verification_focus=verification_focus,
    ) + fail_override_hint

    finished_steps_str = _prune_finished_steps(
        finished_steps=finished_steps,
        prompt_text=trial_prompt_template.replace("{finished_steps_str}", "")
    )

    # 制定最终计划
    prompt = trial_prompt_template.replace(
        "{finished_steps_str}", finished_steps_str)
    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content
    logger.info(f"   📋 [Planner] 计划内容: {content[:200]}")
    # 局部修复护栏：失败窗口内禁止 Planner 输出“全局重写”方案
    if _is_failed_verification(verification) and step_fail_count < MAX_STEP_FAIL and _looks_like_global_rewrite_plan(content):
        logger.info("   ⚠️ [Planner] 检测到全局重写倾向，注入局部修复约束并重试一次")
        hard_local_fix = (
            "\n\n【系统硬约束】\n"
            "你必须仅修复 failed_action / failed_locator，禁止从头重做或全局改写。"
        )
        response = llm.invoke([HumanMessage(content=prompt + hard_local_fix)])
        content = response.content
    # 改进完成判断：当两个标记同时出现时，以【计划已生成】为准
    # (Planner 推理过程中可能先写"【任务已完成】"然后自己推翻，生成新计划)
    has_finished_tag = "【任务已完成】" in content
    has_plan_tag = "【计划已生成】" in content
    is_finished = has_finished_tag and not has_plan_tag
    if has_finished_tag and has_plan_tag:
        logger.info("   ⚠️ [Planner] 检测到【任务已完成】和【计划已生成】同时出现，以计划为准")

    # 消息裁剪：保留最近 M 轮历史对话，避免底层的 State(MessageState) 爆炸
    from config import CONTEXT_MAX_MESSAGE_ROUNDS
    messages_to_keep = []

    current_messages = state.get("messages", [])
    # 每轮对话通常包含一答一问。保留最近的 M * 2 条（如果是奇数也没关系）
    keep_count = CONTEXT_MAX_MESSAGE_ROUNDS * 2 + 1  # 多留一条确保不断层
    if len(current_messages) > keep_count:
        # 把早于保留窗口的消息删除
        to_delete = current_messages[:-keep_count]
        for msg in to_delete:
            if hasattr(msg, "id") and msg.id:
                messages_to_keep.append(RemoveMessage(id=msg.id))

    messages_to_keep.append(response)

    update_dict = {
        "messages": messages_to_keep,
        "plan": content,
        "loop_count": loop_count + 1,
        "is_complete": is_finished,
        "_step_fail_count": step_fail_count
    }
    if verification:
        # Planner 消费后再清理，防止重复计数/状态漂移
        update_dict["verification_result"] = {}

    # 3. 动态路由
    if is_finished:
        # RAG 存储拦截：Planner 判定完成前，检查用户是否要求存入向量数据库
        task_needs_rag = any(kw in task for kw in RAG_GOAL_KEYWORDS)
        rag_already_done = any(
            any(dk in step for dk in RAG_DONE_KEYWORDS) for step in finished_steps
        ) if finished_steps else False

        if task_needs_rag and not rag_already_done:
            logger.info("   📚 [Planner] 用户目标包含向量数据库存储，但尚未执行 → 拦截完成，跳转 RAGNode")
            update_dict["is_complete"] = False
            update_dict["rag_task_type"] = "store_kb"
            return Command(update=update_dict, goto="RAGNode")

        logger.info("🏁 [Planner] 判定任务完成，流程结束。")
        return Command(update=update_dict, goto="__end__")

    # RAG 任务检测（关键词定义在 config.py）
    plan_text = content.lower() if content else ""
    if any(kw in content for kw in RAG_STORE_KEYWORDS):
        logger.info("   📚 [Planner] 检测到 RAG 存储任务 → RAGNode")
        update_dict["rag_task_type"] = "store_kb"
        return Command(update=update_dict, goto="RAGNode")
    elif any(kw in content for kw in RAG_QA_KEYWORDS):
        logger.info("   📚 [Planner] 检测到 RAG 问答任务 → RAGNode")
        update_dict["rag_task_type"] = "qa"
        return Command(update=update_dict, goto="RAGNode")
    else:
        return Command(update=update_dict, goto="CacheLookup")


def coder_node(state: AgentState, config: RunnableConfig, llm) -> Command[Literal["Executor"]]:
    """[Coder] 编写代码"""
    logger.info("\n💻 [Coder] 正在编写代码...")

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
            "_code_source": "llm"
        },
        goto="Executor"
    )


def executor_node(state: AgentState, config: RunnableConfig) -> Command[Literal["Verifier", "Coder", "Planner", "ErrorHandler"]]:
    """[Executor] 执行代码，并根据 _code_source 和错误类型进行分类路由"""
    logger.info("\n⚡ [Executor] 正在执行代码...")
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


def verifier_node(state: AgentState, config: RunnableConfig, llm) -> Command[Literal["Observer", "Planner", "RAGNode"]]:
    """[Verifier] 验收并决定下一步"""
    logger.info("\n🔍 [Verifier] 正在验收...")

    log = state.get("execution_log", "")
    task = state.get("user_task", "")
    current_plan = state.get("plan", "Unknown Plan")
    code_source = state.get("_code_source", "llm")
    current_suggestions = state.get("locator_suggestions", [])

    # 获取最新标签页（处理新标签页打开的情况）
    browser = config["configurable"].get("browser")
    if browser:
        time.sleep(0.3)  # 短暂等待，让新标签页有时间创建
        tab = browser.latest_tab
        # 等待页面加载
        try:
            tab.wait.load_start()
            tab.wait(0.3)
        except TimeoutError as e:
            logger.warning(f"Page load timeout in Verifier: {e}")
        except Exception as e:
            logger.debug(f"Wait interrupted in Verifier: {e}")
        current_url = tab.url if tab else ""
    else:
        tab = None
        current_url = ""

    logger.info(f"   -> 当前验收 URL: {current_url[:60]}...")
    logger.info(f"   📦 代码来源: {code_source}")

    # 1. 快速失败检查（仅致命错误）
    fatal_keywords = ["Runtime Error:", "Traceback", "ElementNotFound",
                      "TimeoutException", "Execution Failed", "Critical"]
    for kw in fatal_keywords:
        if kw in log:
            logger.info(f"⚡ [Verifier] Deterministic Fail: {kw}")

            # 缓存代码失败：跳 Planner，标记失败
            if code_source == "cache":
                return _handle_cache_failure(state, {
                    "messages": [AIMessage(content=f"【缓存验收失败】{kw}")],
                    "reflections": [f"缓存代码验收失败: {kw}"],
                    "verification_result": _build_verification_result(
                        is_success=False,
                        is_done=False,
                        summary=f"缓存代码验收失败: {kw}",
                        source="verifier",
                        failure_scope="local",
                        failed_action=current_plan,
                        evidence=kw,
                        fix_hint="更换执行方式或修复当前失败定位，不要复用该缓存代码",
                    ),
                    "is_complete": False
                })

            # LLM 代码失败：回 Observer
            return Command(
                update={
                    "messages": [AIMessage(content=f"Status: STEP_FAIL ({kw})")],
                    "reflections": [f"Step Failed: {current_plan}. Error: {kw}"],
                    "verification_result": _build_verification_result(
                        is_success=False,
                        is_done=False,
                        summary=f"步骤失败: {kw}",
                        source="verifier",
                        failure_scope="local",
                        failed_action=current_plan,
                        evidence=kw,
                        fix_hint="仅修复当前失败步骤，不要全局重写",
                    ),
                    "is_complete": False
                },
                goto="Observer"
            )

    # 2. LLM 验收（优化 Prompt）
    prompt = VERIFIER_CHECK_PROMPT.format(
        user_task=task,
        current_plan=current_plan,
        current_url=current_url,
        log=log[-2000:],
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content

    parsed = _parse_verifier_result_content(content)
    is_success = parsed["is_success"]
    summary = parsed["summary"]

    # 3. 返回验收结果
    logger.info(f"\n📋 [Verifier] LLM 判定:")
    logger.info(f"   Status: {'SUCCESS' if is_success else 'FAIL'}")
    logger.info(f"   Summary: {summary[:100]}")

    # 将验收结果存入 State，供 main.py 读取和覆盖
    updates = {
        "messages": [response],
        "is_complete": False,  # Verifier 不再判断任务完成，交给 Planner
        "current_url": current_url,
        "verification_result": _build_verification_result(
            is_success=is_success,
            is_done=False,  # 由 Planner 判断
            summary=summary,
            source="verifier",
            failure_scope=parsed.get("failure_scope", "local"),
            failed_action=parsed.get("failed_action", "") or current_plan,
            failed_locator=parsed.get("failed_locator", ""),
            evidence=parsed.get("evidence", ""),
            fix_hint=parsed.get("fix_hint", ""),
        ),
    }

    if is_success:
        updates["finished_steps"] = [summary]
        # 一旦本步成功，释放失败窗口内的缓存黑名单
        updates["_failed_code_cache_ids"] = []
        updates["_failed_dom_cache_ids"] = []
        updates["_cache_hit_id"] = None

        # 检查是否需要存代码或策略到缓存 → RAGNode
        code = state.get("generated_code", "")
        code_source_val = state.get("_code_source", "")
        observer_source = state.get("_observer_source", "")

        needs_store_code = bool(code and len(
            code) > 50 and code_source_val != "cache")
        needs_store_dom = bool(observer_source == "observer")

        if needs_store_code or needs_store_dom:
            logger.info(
                f"   📚 Step OK + 需缓存代码({needs_store_code})/策略({needs_store_dom}) → RAGNode")
            updates["rag_task_type"] = "store_cache"
            return Command(update=updates, goto="RAGNode")

        logger.info("   🔄 Step OK, 继续下一步...")
        return Command(update=updates, goto="Observer")
    else:
        logger.info("   ❌ Step Failed")
        updates["reflections"] = [f"Step Failed: {summary}"]
        failure_scope = _normalize_failure_scope(
            updates["verification_result"].get("failure_scope", "local"))
        # 仅在 global 失败时回滚整条最新策略；local 失败保留上下文做定向修复
        if failure_scope == "global":
            updates["locator_suggestions"] = {
                "__replace__": current_suggestions[:-1]} if current_suggestions else None

        # 缓存代码验收失败：失效缓存 + 跳 Planner
        if code_source == "cache":
            return _handle_cache_failure(state, updates)

        # LLM 代码失败：回 Observer 重试
        return Command(update=updates, goto="Observer")
