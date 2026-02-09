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
from prompts.action_prompts import ACTION_CODE_GEN_PROMPT
from prompts.planner_prompts import PLANNER_START_PROMPT, PLANNER_STEP_PROMPT, PLANNER_CONTINUE_PROMPT

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
    # urlparse 已在文件顶部导入

    # 1. 延续关键词检测
    CONTINUE_KEYWORDS = ["继续", "接着", "下一页", "翻页", "再爬", "追加", "补充", "当前页面"]
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
            # re 已在文件顶部导入
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
# [V4] 代码缓存检索节点
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

    # [V4] 检查本轮是否已有缓存失败（防止死循环）
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

    task = state.get("user_task", "")
    plan = state.get("plan", "")  # [V4] 新增 plan 作为查询条件
    dom_skeleton = state.get("dom_skeleton", "")
    current_url = state.get("current_url", "")

    # 空白页/初始页面，跳过缓存检索
    if not current_url or current_url.startswith(("about:", "data:", "chrome://")):
        logger.info("   ⏭️ 初始页面，跳过缓存检索")
        return Command(
            update={"_code_source": "llm"},
            goto="Coder"
        )

    try:
        from skills.code_cache import code_cache_manager

        # [V4] 使用 plan + task 组合查询
        combined_task = f"{task}\n当前计划: {plan}" if plan else task

        hits = code_cache_manager.search(
            task=combined_task,
            dom_skeleton=dom_skeleton,
            url=current_url,
            top_k=3
        )

        if hits and hits[0].score >= CODE_CACHE_THRESHOLD:
            best_hit = hits[0]
            logger.info(
                f"   ✅ 命中缓存! Score: {best_hit.score:.4f}, URL: {best_hit.url_pattern}")
            logger.info(f"   📋 原任务: {best_hit.goal[:50]}...")

            # 直接使用缓存代码，跳到 Executor
            return Command(
                update={
                    "generated_code": best_hit.code,
                    "messages": [AIMessage(content=f"【缓存命中】复用历史代码 (Score: {best_hit.score:.4f})")],
                    "_code_source": "cache",  # [V4] 标记代码来源
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
        return

    # [V4] 如果是缓存代码执行成功，不重复存储
    code_source = state.get("_code_source")
    if code_source == "cache":
        logger.info("   ⏭️ [CodeCache] 缓存代码执行，跳过存储")
        return

    code = state.get("generated_code", "")
    if not code or len(code) < 50:
        logger.info("   ⏭️ [CodeCache] 代码过短，跳过存储")
        return

    # [V4] 使用 plan 作为 goal
    goal = state.get("plan", "")
    dom_skeleton = state.get("dom_skeleton", "")

    try:
        from skills.code_cache import code_cache_manager

        cache_id = code_cache_manager.save(
            goal=goal,  # [V4] 改为 goal
            dom_skeleton=dom_skeleton,
            url=current_url,
            code=code
        )

        if cache_id:
            logger.info(f"   💾 [CodeCache] 代码已缓存: {cache_id}")
    except Exception as e:
        logger.info(f"   ⚠️ [CodeCache] 存储失败: {e}")


def error_handler_node(state: AgentState, config: RunnableConfig, llm) -> Command[Literal["Observer", "__end__"]]:
    """
    [ErrorHandler] 全局错误处理与回退
    当其他节点发生不可恢复的错误时跳转至此
    """
    logger.info("\n🚑 [ErrorHandler] 检测到严重错误，正在尝试恢复...")

    error_msg = state.get("error", "Unknown Error")
    reflections = state.get("reflections", [])

    # 构建回退策略
    prompt = f"""
    系统在执行过程中遇到严重错误。
    【错误信息】{error_msg}
    【已尝试的反思】{reflections[-1] if reflections else 'None'}
    
    请分析是否可以重试或必须终止任务。
    如果可以重试，请给出建议。
    如果必须终止，请说明原因。
    
    Status: [RETRY | TERMINATE]
    Strategy: [策略描述]
    """

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

    # [V4] 新一轮开始，重置缓存失败标记
    base_update = {"_cache_failed_this_round": False}

    # 获取浏览器实例
    browser = config["configurable"].get("browser")
    if not browser:
        logger.info("   ⚠️ 无浏览器实例，跳过观察")
        return Command(update=base_update, goto="Planner")

    # [V3 Fix] 先等待新标签页稳定，再获取最新标签页
    # time 已在文件顶部导入
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

    # [V3 Fix] 在页面加载后再获取 URL（确保是新页面的 URL）
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
        # hashlib 已在文件顶部导入
        current_dom_hash = hashlib.md5(dom.encode()).hexdigest()
        previous_dom_hash = state.get("dom_hash", "")

        # 获取历史累积的策略列表
        accumulated_strategies = state.get("locator_suggestions", [])

        # [V3 Fix] 检查是否有失败记录，有则强制重新分析（之前的策略可能是错的）
        reflections = state.get("reflections", [])
        error_type = state.get("error_type")
        has_failure = len(reflections) > 0 or error_type is not None

        # 只有当 DOM 发生变化 或 存在失败记录时，才进行视觉分析
        should_analyze = (current_dom_hash != previous_dom_hash) or has_failure
        new_strategy_entry = None

        if should_analyze:
            if has_failure and current_dom_hash == previous_dom_hash:
                logger.info(f"   🔄 [Observer] 检测到失败记录，强制重新分析 DOM...")
                # 清空之前可能错误的策略
                accumulated_strategies = []
            logger.info(
                f"   -> 正在进行视觉定位分析 (Context: {len(finished_steps)} finished steps)...")
            locator_suggestions = observer.analyze_locator_strategy(
                dom, task, current_url, previous_steps=finished_steps)

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

        # [V4] 合并基础更新
        update_dict = {
            **base_update,
            "dom_skeleton": dom,
            "dom_hash": current_dom_hash,
            "current_url": current_url,
            "locator_suggestions": [new_strategy_entry] if new_strategy_entry else []
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


def planner_node(state: AgentState, config: RunnableConfig, llm) -> Command[Literal["CacheLookup", "__end__"]]:
    """[Planner] 负责制定下一步计划（环境感知已由 Observer 完成）"""
    logger.info("\n🧠 [Planner] 正在制定计划...")
    tab = _get_tab(config)

    task = state["user_task"]
    loop_count = state.get("loop_count", 0)
    finished_steps = state.get("finished_steps", [])

    # [V3] 循环限制：防止死循环
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

        # [V5] 任务连续性检测：判断是延续任务还是全新任务
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
                    # [V5] 全新任务：重置所有旧状态（使用 None 触发 clearable_list_reducer 清空）
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

    finished_steps_str = "\n".join(
        [f"- {s}" for s in finished_steps]) if finished_steps else "(无)"

    # 2. 制定计划
    prompt = PLANNER_STEP_PROMPT.format(
        task=task,
        current_url=current_url,
        finished_steps_str=finished_steps_str,
        suggestions_str=suggestions_str,
        reflection_str=reflection_str
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content
    is_finished = "【任务已完成】" in content

    update_dict = {
        "messages": [response],
        "plan": content,
        "loop_count": loop_count + 1,
        "is_complete": is_finished
    }

    # 3. 动态路由
    if is_finished:
        logger.info("🏁 [Planner] 判定任务完成，流程结束。")
        return Command(update=update_dict, goto="__end__")
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

    prompt = f"""
    ⚠️ **【唯一任务】** - 你必须且只能完成以下计划，禁止做任何其他事情！
    {plan}

    ---
    {base_prompt}
    """

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
            "_code_source": "llm"  # [V4] 明确标记为 LLM 生成
        },
        goto="Executor"
    )


def executor_node(state: AgentState, config: RunnableConfig) -> Command[Literal["Verifier", "Coder", "Planner", "ErrorHandler"]]:
    """[Executor] 执行代码，并根据 _code_source 和错误类型进行分类路由"""
    logger.info("\n⚡ [Executor] 正在执行代码...")
    tab = _get_tab(config)
    code = state.get("generated_code", "")
    code_source = state.get("_code_source", "llm")  # [V4] 获取代码来源

    logger.info(f"   📦 代码来源: {code_source}")

    # [V3] 错误分类关键词
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

        # [V3] 检查执行日志中是否有错误（即使没有抛异常）
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

            # [V4] 缓存代码失败：直接跳 Planner，不尝试 Coder 修复
            if code_source == "cache":
                logger.info(
                    f"   ⚠️ 缓存代码失败，标记 _cache_failed_this_round，跳 Planner")
                return Command(
                    update={
                        "messages": [AIMessage(content=f"【缓存代码失败】{error_kw}，重新规划")],
                        "execution_log": execution_log,
                        "_cache_failed_this_round": True,
                        "reflections": [f"缓存代码失败: {error_kw}，需要重新生成"]
                    },
                    goto="Planner"
                )

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


def verifier_node(state: AgentState, config: RunnableConfig, llm) -> Command[Literal["Observer", "Planner"]]:
    """[Verifier] 验收并决定下一步 (V4: Planner 是唯一出口)"""
    logger.info("\n🔍 [Verifier] 正在验收...")

    log = state.get("execution_log", "")
    task = state.get("user_task", "")
    current_plan = state.get("plan", "Unknown Plan")
    code_source = state.get("_code_source", "llm")  # [V4] 获取代码来源

    # [V3 Fix] 获取最新标签页（处理新标签页打开的情况）
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

            # [V4] 缓存代码失败：跳 Planner，标记失败
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
    prompt = f"""
    你是自动化测试验收员。请根据以下信息判断**当前步骤**是否成功。
    
    【当前计划】{current_plan}
    【当前 URL】{current_url}
    【执行日志】{log[-2000:]}
    
    【验收原则】
    1. **只验收当前步骤**: 你只需判断【当前计划】描述的操作是否执行成功，**严禁**评价整体任务是否完成！
    2. **Warning 不算失败**: "Warning:"、"Failed to wait"、"没有等到新标签页" 等提示只是警告，不影响步骤成功
    3. **关注操作结果**: 判断计划中的核心操作是否执行成功
    4. **禁止越权判断**: 严禁在 Summary 中说"核心任务已完成"、"整体目标已达成"等整体性评价，这不是你的职责！
    
    格式:
    Status: [STEP_SUCCESS | STEP_FAIL]
    Summary: [简短描述当前步骤的执行结果，不要评价整体任务]
    """
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
        "is_complete": False,  # [V4] Verifier 不再判断任务完成，交给 Planner
        "current_url": current_url,
        "verification_result": {
            "is_success": is_success,
            "is_done": False,  # [V4] 由 Planner 判断
            "summary": summary
        }
    }

    if is_success:
        updates["finished_steps"] = [summary]

        # [V4] 成功时存入缓存（无论 cache 还是 llm 来源都存）
        _save_code_to_cache(state, current_url)

        logger.info("   🔄 Step OK, 继续下一步...")
        return Command(update=updates, goto="Observer")
    else:
        logger.info("   ❌ Step Failed")
        updates["reflections"] = [f"Step Failed: {summary}"]

        # [V4] 缓存代码验收失败：跳 Planner
        if code_source == "cache":
            updates["_cache_failed_this_round"] = True
            return Command(update=updates, goto="Planner")

        # LLM 代码失败：回 Observer 重试
        return Command(update=updates, goto="Observer")
