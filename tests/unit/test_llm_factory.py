from __future__ import annotations

import pytest

import core.llm_factory as factory


class _FakeChatOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_optional_thinking_mode_is_forwarded_and_part_of_cache(monkeypatch):
    monkeypatch.setattr(factory, "ChatOpenAI", _FakeChatOpenAI)
    monkeypatch.setattr(factory.httpx, "Client", lambda **kwargs: object())
    factory._llm_cache.clear()
    monkeypatch.setenv("LLM_ENABLE_THINKING", "false")

    first = factory.create_llm("model", "key", "https://example.test/v1")
    assert first.kwargs["extra_body"] == {"enable_thinking": False}
    assert first.kwargs["stream_usage"] is True

    monkeypatch.setenv("LLM_ENABLE_THINKING", "true")
    second = factory.create_llm("model", "key", "https://example.test/v1")
    assert second is not first
    assert second.kwargs["extra_body"] == {"enable_thinking": True}


def test_invalid_thinking_mode_fails_fast(monkeypatch):
    monkeypatch.setenv("LLM_ENABLE_THINKING", "sometimes")
    with pytest.raises(ValueError, match="LLM_ENABLE_THINKING"):
        factory._configured_thinking_mode()
