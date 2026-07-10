import pytest

from app.providers.llm.base import LLMResponse
from app.providers.llm.online import OnlineSearchLLM


class _FakeOpenRouterPrimary:
    """Mimics OpenAICompatibleProvider(name='openrouter') -- only complete()."""

    def __init__(self, default_model="anthropic/claude-3.5-haiku"):
        self.name = "openrouter"
        self.default_model = default_model
        self.calls: list[str | None] = []

    def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
        self.calls.append(model)
        return LLMResponse(content="ok", model=model or "", provider=self.name)


class _FakeAnthropicPrimary:
    """Mimics AnthropicProvider -- complete() accepts a `tools` kwarg."""

    def __init__(self, default_model="claude-default"):
        self.name = "anthropic"
        self.default_model = default_model
        self.captured: dict = {}

    def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None, tools=None):
        self.captured = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "tools": tools,
        }
        return LLMResponse(content="anthropic native search answer", model="claude-x", provider=self.name)


class _FakeOpenAIPrimary:
    """Mimics OpenAICompatibleProvider(name='openai') -- has complete_native_search()."""

    def __init__(self, default_model="gpt-default"):
        self.name = "openai"
        self.default_model = default_model
        self.captured: dict = {}

    def complete_native_search(self, messages, *, model=None, temperature=0.7, max_tokens=None):
        self.captured = {"model": model, "temperature": temperature, "max_tokens": max_tokens}
        return LLMResponse(content="openai native search answer", model="gpt-x", provider=self.name)


class _FakeUnsupportedPrimary:
    def __init__(self, name="nvidia", default_model="some-model"):
        self.name = name
        self.default_model = default_model

    def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
        raise AssertionError("should never be called for an unsupported provider")


def test_openrouter_appends_online_suffix_using_default_model():
    primary = _FakeOpenRouterPrimary()
    llm = OnlineSearchLLM(primary)
    llm.complete([])
    assert primary.calls == ["anthropic/claude-3.5-haiku:online"]


def test_openrouter_does_not_double_suffix_explicit_model():
    primary = _FakeOpenRouterPrimary()
    llm = OnlineSearchLLM(primary)
    llm.complete([], model="openai/gpt-4o:online")
    assert primary.calls == ["openai/gpt-4o:online"]


def test_anthropic_passes_web_search_server_tool():
    primary = _FakeAnthropicPrimary()
    llm = OnlineSearchLLM(primary)
    resp = llm.complete([], temperature=0.3, max_tokens=500)

    assert resp.content == "anthropic native search answer"
    assert primary.captured["tools"] == [{"type": "web_search_20260209", "name": "web_search"}]
    assert primary.captured["temperature"] == 0.3
    assert primary.captured["max_tokens"] == 500
    # no ":online" suffix trick for anthropic -- model passed through as-is (None -> provider default)
    assert primary.captured["model"] is None


def test_openai_dispatches_to_complete_native_search():
    primary = _FakeOpenAIPrimary()
    llm = OnlineSearchLLM(primary)
    resp = llm.complete([], model="gpt-5.6", temperature=0.5, max_tokens=1000)

    assert resp.content == "openai native search answer"
    assert primary.captured == {"model": "gpt-5.6", "temperature": 0.5, "max_tokens": 1000}


def test_rejects_unsupported_provider():
    with pytest.raises(ValueError, match="not supported"):
        OnlineSearchLLM(_FakeUnsupportedPrimary(name="nvidia"))
