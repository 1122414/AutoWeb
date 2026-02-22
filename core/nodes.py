import json
import time
import hashlib
import re
import traceback
from typing import Literal, Union
from urllib.parse import urlparse
from langchain_core.messages import HumanMessage, AIMessage
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


# ==============================================================================
# 代码缓存检索节点
# ==============================================================================
def cache_lookup_node(state: AgentState, config: RunnableConfig) -> Command[Literal["Coder", "Executor"]]:
    """
    [CacheLookup] 尝试从缓存中检索可复用的代码

    策略:
    - 检查 _cache_failed_this_round，若为 True 则强制跳过
    - 使用 plan + task + dom_skeleton + url 构建检索 Query
    - 命中时设置 _code_source = "cache"，跳到 Executor
    - 未命中时设置 _code_source = "llm"，跳到 Coder
    """
    from config import CODE_CACHE_ENABLED, CODE_CACHE_THRESHOLD

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
            top_k=3
        )

        if hits and hits[0].score >= CODE_CACHE_THRESHOLD:
            best_hit = hits[0]
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
            if hits:
                logger.info(
                    f"   ❌ 最高分 {hits[0].score:.4f} 低于阈值 {CODE_CACHE_THRESHOLD}")
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


def _handle_cache_failure(state: AgentState, updates: dict) -> Command:
    """缓存代码失败统一处理：记录失败 + 标记熔断 + 跳 Planner

    调用方负责构建 updates 中的 messages / reflections 等字段，
    本函数只负责：记录失败 + 追加熔断标记。
    """
    cache_hit_id = state.get("_cache_hit_id", "")
    if cache_hit_id:
        try:
            from skills.code_cache import code_cache_manager
            code_cache_manager.record_failure(cache_hit_id, reason="执行/验收失败")
        except Exception as e:
            logger.info(f"   ⚠️ [CodeCache] 记录失败异常: {e}")

    updates["_cache_failed_this_round"] = True
    updates["_cache_hit_id"] = None
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

    updates = {
        "messages": [AIMessage(content=f"【系统故障】正在恢复...\n{content}")],
        # 清除错误标志，以便重试
        "error": None
    }

    if is_terminate:
        logger.info("   ❌ ErrHandler: 决定终止任务。")
        updates["is_complete"] = True  # 虽然失败了，但也算结束
        return Command(update=updates, goto="__end__")
    else:
        logger.info("   🔄 ErrHandler: 尝试回退到 Observer 重新感知环境。")
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

        # 检查是否有失败记录，有则强制重新分析（之前的策略可能是错的）
        reflections = state.get("reflections", [])
        error_type = state.get("error_type")
        has_failure = len(reflections) > 0 or error_type is not None

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
                    dom_cache_manager.record_failure(
                        dom_cache_hit_id, reason="后续执行失败")
            except Exception as e:
                logger.info(f"   ⚠️ [DomCache] 记录失败异常: {e}")

        # DOM Cache: 仅在需要分析且无失败记录时尝试命中
        dom_cache_hit = None
        if should_analyze and not has_failure:
            try:
                from config import DOM_CACHE_ENABLED, DOM_CACHE_THRESHOLD, DOM_CACHE_TOP_K
                if DOM_CACHE_ENABLED:
                    from skills.dom_cache import dom_cache_manager
                    cache_hits = dom_cache_manager.search(
                        user_task=task,
                        current_url=current_url,
                        dom_skeleton=dom,
                        top_k=DOM_CACHE_TOP_K,
                    )
                    if cache_hits and cache_hits[0].score >= DOM_CACHE_THRESHOLD:
                        dom_cache_hit = cache_hits[0]
                        logger.info(
                            f"   ✅ [DomCache] 命中缓存 score={dom_cache_hit.score:.4f}, "
                            f"url={dom_cache_hit.url_pattern}"
                        )
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
                locator_suggestions = observer.analyze_locator_strategy(
                    dom, task, current_url, previous_steps=finished_steps, ignore_cache=has_failure)

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
        }

        # 新分析结果写入 DomCache
        if new_strategy_entry and not dom_cache_hit:
            try:
                from config import DOM_CACHE_ENABLED
                if DOM_CACHE_ENABLED:
                    from skills.dom_cache import dom_cache_manager
                    strategies = new_strategy_entry.get("strategies", [])
                    if isinstance(strategies, dict):
                        strategies = [strategies]
                    if isinstance(strategies, list) and strategies:
                        dom_cache_manager.save(
                            user_task=task,
                            current_url=current_url,
                            dom_skeleton=dom,
                            locator_suggestions=strategies,
                        )
                        logger.info("   💾 [DomCache] 已提交缓存写入任务")
            except Exception as e:
                logger.info(f"   ⚠️ [DomCache] 写入异常: {e}")

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
    - store_code: 将验证通过的代码存入 Code Cache
    - qa: 查询知识库并返回答案
    """
    rag_task = state.get("rag_task_type")
    logger.info(f"\n📚 [RAG Node] 任务类型: {rag_task}")

    result_summary = ""

    try:
        if rag_task == "store_kb":
            result_summary = _rag_store_kb(state)

        elif rag_task == "store_code":
            result_summary = _rag_store_code(state, config)

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
            "finished_steps": [result_summary] if rag_task != "store_code" else [],
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


def _rag_store_code(state: AgentState, config: RunnableConfig) -> str:
    """[RAG] 将验证通过的代码存入 Code Cache"""
    current_url = state.get("current_url", "")
    result = _save_code_to_cache(state, current_url)
    if "false" in result:
        return f"代码保存失败: {result['false']}"
    else:
        return f"代码已提交缓存存储"


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
    MAX_LOOP_COUNT = 10
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
                    "_observer_source": None,       # 清空观察来源
                    "_dom_cache_hit_id": None,      # 清空 DomCache 命中 ID
                    "dom_skeleton": "",             # 清空 DOM（Observer 会重新获取）
                    "dom_hash": None,               # 清空 DOM 哈希
                    "loop_count": 1,                # 从 1 开始（因为这是第一次规划）
                    "is_complete": False
                },
                goto="CacheLookup"
            )

    # 1. 从 State 读取 Observer 提供的定位策略（不再自己调用 observer）
    accumulated_strategies = state.get("locator_suggestions", [])
    if accumulated_strategies:
        suggestions_str = json.dumps(
            accumulated_strategies, ensure_ascii=False, indent=2)
    else:
        suggestions_str = "无特定定位建议，请自行分析 DOM。"

    reflections = state.get("reflections", [])
    reflection_str = ""
    if reflections:
        reflection_str = "\n⚠️ **之前的失败教训 (请在规划时重点规避)**:\n" + \
            "\n".join([f"- {r}" for r in reflections])

    verification = state.get("verification_result", {})
    last_verification = verification.get(
        "summary", "(无)") if verification else "(无)"

    # 连续失败保底：跟踪连续步骤失败次数
    step_fail_count = state.get("_step_fail_count", 0)
    is_last_step_fail = verification.get(
        "is_success", True) is False if verification else False
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

    finished_steps_str = "\n".join(
        [f"- {s}" for s in finished_steps]) if finished_steps else "(无)"

    # 2. 制定计划
    prompt = PLANNER_STEP_PROMPT.format(
        task=task,
        current_url=current_url,
        finished_steps_str=finished_steps_str,
        suggestions_str=suggestions_str,
        reflection_str=reflection_str,
        last_verification=last_verification
    ) + fail_override_hint
    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content
    # 改进完成判断：当两个标记同时出现时，以【计划已生成】为准
    # (Planner 推理过程中可能先写"【任务已完成】"然后自己推翻，生成新计划)
    has_finished_tag = "【任务已完成】" in content
    has_plan_tag = "【计划已生成】" in content
    is_finished = has_finished_tag and not has_plan_tag
    if has_finished_tag and has_plan_tag:
        logger.info("   ⚠️ [Planner] 检测到【任务已完成】和【计划已生成】同时出现，以计划为准")

    update_dict = {
        "messages": [response],
        "plan": content,
        "loop_count": loop_count + 1,
        "is_complete": is_finished,
        "_step_fail_count": step_fail_count
    }

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
                            "error_type": "syntax_max_retry"
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
                        "reflections": [f"定位错误: {error_kw}，需要重新分析页面"]
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
                "reflections": [f"Execution crashed: {str(e)}"]
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
                return Command(
                    update={
                        "messages": [AIMessage(content=f"【缓存验收失败】{kw}")],
                        "_cache_failed_this_round": True,
                        "reflections": [f"缓存代码验收失败: {kw}"],
                        "is_complete": False
                    },
                    goto="Planner"
                )

            # LLM 代码失败：回 Observer
            return Command(
                update={
                    "messages": [AIMessage(content=f"Status: STEP_FAIL ({kw})")],
                    "reflections": [f"Step Failed: {current_plan}. Error: {kw}"],
                    "is_complete": False
                },
                goto="Observer"
            )

    # 2. LLM 验收（优化 Prompt）
    prompt = VERIFIER_CHECK_PROMPT.format(
        current_plan=current_plan,
        current_url=current_url,
        log=log[-2000:],
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content

    is_success = "Status: STEP_SUCCESS" in content

    summary = "Step executed."
    for line in content.split("\n"):
        if line.startswith("Summary:"):
            summary = line.replace("Summary:", "").strip()

    # 3. 返回验收结果
    logger.info(f"\n📋 [Verifier] LLM 判定:")
    logger.info(f"   Status: {'SUCCESS' if is_success else 'FAIL'}")
    logger.info(f"   Summary: {summary[:100]}")

    # 将验收结果存入 State，供 main.py 读取和覆盖
    updates = {
        "messages": [response],
        "is_complete": False,  # Verifier 不再判断任务完成，交给 Planner
        "current_url": current_url,
        "verification_result": {
            "is_success": is_success,
            "is_done": False,  # 由 Planner 判断
            "summary": summary
        }
    }

    if is_success:
        updates["finished_steps"] = [summary]

        # 检查是否需要存代码到缓存 → RAGNode
        code = state.get("generated_code", "")
        code_source_val = state.get("_code_source", "")
        if code and len(code) > 50 and code_source_val != "cache":
            logger.info("   📚 Step OK + 需缓存代码 → RAGNode")
            updates["rag_task_type"] = "store_code"
            return Command(update=updates, goto="RAGNode")

        logger.info("   🔄 Step OK, 继续下一步...")
        return Command(update=updates, goto="Observer")
    else:
        logger.info("   ❌ Step Failed")
        updates["reflections"] = [f"Step Failed: {summary}"]

        # 缓存代码验收失败：失效缓存 + 跳 Planner
        if code_source == "cache":
            return _handle_cache_failure(state, updates)

        # LLM 代码失败：回 Observer 重试
        return Command(update=updates, goto="Observer")
