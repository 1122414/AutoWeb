from __future__ import annotations

from langchain_core.messages import HumanMessage
from core.nodes._utils import _count_tokens, _get_summarizer_llm
from skills.logger import logger
from skills.run_trace import traced_llm_invoke

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
        resp = traced_llm_invoke(
            summarizer,
            [HumanMessage(content=summary_prompt)],
            node="ContextSummarizer",
        )
        early_summary = resp.content.strip()
    except Exception as e:
        logger.warning(f"   ⚠️ [Context] 摘要压缩失败: {e}，使用截断兜底")
        early_summary = f"(已完成 {len(early)} 个早期步骤)"

    recent_str = "\n".join([f"- {s}" for s in recent])
    result = f"[早期摘要] {early_summary}\n[最近步骤]\n{recent_str}"

    new_tokens = _count_tokens(prompt_text.replace(finished_steps_str, result))
    logger.info(f"   ✅ [Context] 裁剪后 Token: {new_tokens}/{threshold}")

    return result
