"""通用工具函数：配置解析、浏览器操作、时间处理、URL解析、Token统计。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import tiktoken
from langchain_core.runnables import RunnableConfig

from skills.logger import logger


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
    from config import CONTINUE_KEYWORDS

    for kw in CONTINUE_KEYWORDS:
        if kw in new_task:
            logger.info(f"   🔗 [TaskContinuity] 检测到延续关键词: '{kw}' → 保留旧状态")
            return True

    if current_url:
        try:
            current_domain = urlparse(current_url).netloc
            if current_domain and current_domain in new_task:
                logger.info(
                    f"   🔗 [TaskContinuity] 任务中包含当前域名 '{current_domain}' → 保留旧状态")
                return True

            urls_in_task = re.findall(r'https?://[^\s<>"\']+', new_task)
            for url in urls_in_task:
                task_domain = urlparse(url).netloc
                if task_domain and task_domain != current_domain:
                    logger.info(
                        f"   🆕 [TaskContinuity] 任务指向新域名 '{task_domain}' (当前: '{current_domain}') → 全新任务")
                    return False
        except Exception as e:
            logger.info(f"   ⚠️ [TaskContinuity] URL 解析失败: {e}")

    logger.info(f"   🆕 [TaskContinuity] 无明确延续标志 → 视为全新任务，清空旧状态")
    return False


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
