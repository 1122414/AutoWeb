# ==============================================================================
# LLM 工厂 - 统一创建 & 复用 ChatOpenAI 实例
# ==============================================================================

import os

from langchain_openai import ChatOpenAI

import httpx
from skills.logger import logger, trace_log

# 缓存：相同配置复用同一实例，避免重复创建
_llm_cache: dict = {}


def _configured_thinking_mode() -> bool | None:
    """Return the optional provider-compatible thinking-mode override."""
    raw = os.getenv("LLM_ENABLE_THINKING")
    if raw is None or not raw.strip():
        return None
    value = raw.strip().lower()
    if value in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise ValueError(
        "LLM_ENABLE_THINKING must be a boolean value such as true or false"
    )


def create_llm(
    model_name: str,
    api_key: str,
    base_url: str,
    temperature: float = 0,
    streaming: bool = True
) -> ChatOpenAI:
    """
    创建 ChatOpenAI 实例，相同配置自动复用。

    Args:
        model_name: 模型名称
        api_key: API Key
        base_url: API Base URL
        temperature: 温度参数
        streaming: 是否启用流式输出

    Returns:
        ChatOpenAI 实例
    """
    enable_thinking = _configured_thinking_mode()
    cache_key = (
        model_name,
        api_key,
        base_url,
        temperature,
        streaming,
        enable_thinking,
    )

    if cache_key not in _llm_cache:
        trace_log(f"创建新 LLM 实例: model={model_name}, base_url={base_url}, streaming={streaming}")
        # 配置 httpx Client 来增加超时防流式断流
        http_client = httpx.Client(timeout=180.0)
        options = {}
        if enable_thinking is not None:
            options["extra_body"] = {"enable_thinking": enable_thinking}
        _llm_cache[cache_key] = ChatOpenAI(
            model=model_name,
            temperature=temperature,
            openai_api_key=api_key,
            openai_api_base=base_url,
            streaming=streaming,
            stream_usage=True,
            max_retries=3,
            http_client=http_client,
            **options,
        )
        logger.info(f"   ✅ [create_llm] LLM 实例已创建: {model_name} (缓存 {len(_llm_cache)} 个)")
    else:
        trace_log(f"复用已缓存 LLM 实例: model={model_name} (缓存 {len(_llm_cache)} 个)")

    return _llm_cache[cache_key]
