# ==============================================================================
# LLM 工厂 - 统一创建 & 复用 ChatOpenAI 实例
# ==============================================================================

from langchain_openai import ChatOpenAI

# 缓存：相同配置复用同一实例，避免重复创建
_llm_cache: dict = {}


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
    cache_key = (model_name, api_key, base_url, temperature, streaming)

    if cache_key not in _llm_cache:
        _llm_cache[cache_key] = ChatOpenAI(
            model=model_name,
            temperature=temperature,
            openai_api_key=api_key,
            openai_api_base=base_url,
            streaming=streaming
        )

    return _llm_cache[cache_key]
