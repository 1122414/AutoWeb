from __future__ import annotations

import json
from typing import Literal

from langchain_core.messages import HumanMessage, AIMessage, RemoveMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from core.state_v2 import AgentState
from core.nodes._utils import _get_tab, _detect_task_continuity
from core.nodes._context import _prune_locator_suggestions, _prune_finished_steps
from core.nodes._verification import _is_failed_verification, _verification_focus_text
from config import RAG_STORE_KEYWORDS, RAG_QA_KEYWORDS, RAG_GOAL_KEYWORDS, RAG_DONE_KEYWORDS
from prompts.planner_prompts import PLANNER_START_PROMPT, PLANNER_STEP_PROMPT, PLANNER_CONTINUE_PROMPT, PLANNER_FORCE_SKIP_PROMPT
from skills.logger import logger


def _looks_like_global_rewrite_plan(plan_text: str) -> bool:
    text = (plan_text or "").lower()
    keywords = ["全局", "全部重写", "从头", "重做", "重写", "推翻", "重新执行全部", "重来"]
    return any(kw in text for kw in keywords)


def _planner_completion_is_premature(task: str, finished_steps: list) -> bool:
    task_text = str(task or "").lower()
    steps_text = "\n".join(str(step or "") for step in (finished_steps or [])).lower()
    task_markers = [
        "爬取", "抓取", "采集", "提取", "获取", "简介", "详情", "小说信息", "榜单",
        "scrape", "extract", "detail", "description",
    ]
    task_markers += [
        word.encode("utf-8").decode("latin1", errors="ignore")
        for word in task_markers
        if any(ord(ch) > 127 for ch in word)
    ]
    requires_data = any(kw in task_text for kw in task_markers)
    if not requires_data:
        return False

    done_markers = [
        "爬取", "抓取", "采集", "提取", "保存", "简介", "详情", "小说信息",
        "extract", "scrape", "saved", "description",
    ]
    done_markers += [
        word.encode("utf-8").decode("latin1", errors="ignore")
        for word in done_markers
        if any(ord(ch) > 127 for ch in word)
    ]
    data_done = any(kw in steps_text for kw in done_markers)
    return not data_done


def _planner_forced_extract_plan(task: str) -> str:
    task_text = str(task or "")
    if any(kw in task_text for kw in ["简介", "详情", "detail", "description"]):
        return (
            "【计划已生成】\n"
            "1. 提取当前榜单页可见的小说列表信息，包括排名、书名、作者、分类、链接等字段，"
            "为后续进入详情页获取简介做准备。"
        )
    return (
        "【计划已生成】\n"
        "1. 提取当前页面可见的榜单列表信息，并保留条目链接用于后续处理。"
    )


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
                    "generated_action": None,
                    "execution_mode": None,
                    "dpcli_result": None,
                    "dpcli_snapshot": None,
                    "dpcli_snapshot_view": None,
                    "dpcli_detail_batch_ran": False,
                    "execution_log": None,          # 清空执行日志
                    "verification_result": None,    # 清空验收结果
                    "error": None,                  # 清空错误信息
                    "error_type": None,             # 清空错误类型
                    "coder_retry_count": 0,         # 重置重试计数
                    "_code_source": None,           # 清空代码来源
                    "_action_source": None,
                    "_action_cache_hit_id": None,
                    "_failed_action_cache_ids": [],
                    "_dpcli_action_disabled": False,
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
    if is_finished and _planner_completion_is_premature(task, finished_steps):
        logger.info("   ⚠️ [Planner] 拦截过早完成：目标仍包含数据/详情提取，继续生成提取计划")
        content = _planner_forced_extract_plan(task)
        response = AIMessage(content=content)
        is_finished = False

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
