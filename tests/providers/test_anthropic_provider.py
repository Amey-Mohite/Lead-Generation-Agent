from types import SimpleNamespace

from app.providers.llm.anthropic_provider import AnthropicProvider
from app.providers.llm.base import ChatMessage


class _FakeMessages:
    def __init__(self, captured):
        self._captured = captured

    def create(self, **kwargs):
        self._captured.update(kwargs)
        return SimpleNamespace(
            model="claude-x",
            content=[SimpleNamespace(type="text", text="hi from claude")],
            usage=SimpleNamespace(input_tokens=5, output_tokens=3),
            stop_reason="end_turn",
        )


class _FakeClient:
    def __init__(self, captured):
        self.messages = _FakeMessages(captured)


def test_complete_splits_system_and_maps_usage():
    captured: dict = {}
    provider = AnthropicProvider(default_model="claude-default", client=_FakeClient(captured))
    resp = provider.complete(
        [
            ChatMessage(role="system", content="be brief"),
            ChatMessage(role="user", content="hello"),
        ]
    )

    assert resp.content == "hi from claude"
    assert resp.provider == "anthropic"
    assert resp.prompt_tokens == 5
    assert resp.completion_tokens == 3
    assert resp.finish_reason == "end_turn"
    # system pulled out; only non-system messages in conversation
    assert captured["system"] == "be brief"
    assert captured["messages"] == [{"role": "user", "content": "hello"}]
    # anthropic requires max_tokens -> default applied
    assert captured["max_tokens"] == 1024
    assert captured["model"] == "claude-default"


def test_complete_omits_tools_by_default():
    captured: dict = {}
    provider = AnthropicProvider(default_model="claude-default", client=_FakeClient(captured))
    provider.complete([ChatMessage(role="user", content="hi")])
    assert "tools" not in captured


def test_complete_passes_tools_through_when_given():
    captured: dict = {}
    provider = AnthropicProvider(default_model="claude-default", client=_FakeClient(captured))
    tools = [{"type": "web_search_20260209", "name": "web_search"}]
    provider.complete([ChatMessage(role="user", content="hi")], tools=tools)
    assert captured["tools"] == tools
