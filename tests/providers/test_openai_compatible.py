from types import SimpleNamespace

from app.providers.llm.base import ChatMessage
from app.providers.llm.openai_compatible import OpenAICompatibleProvider


class _FakeCompletions:
    def __init__(self, captured):
        self._captured = captured

    def create(self, **kwargs):
        self._captured.update(kwargs)
        return SimpleNamespace(
            model="resolved-model",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="hello there"),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
        )


class _FakeClient:
    def __init__(self, captured):
        self.chat = SimpleNamespace(completions=_FakeCompletions(captured))


def test_complete_maps_response_and_passes_args():
    captured: dict = {}
    provider = OpenAICompatibleProvider(
        name="openrouter", default_model="default-model", client=_FakeClient(captured)
    )
    resp = provider.complete(
        [ChatMessage(role="user", content="hi")], temperature=0.2, max_tokens=100
    )

    assert resp.content == "hello there"
    assert resp.provider == "openrouter"
    assert resp.model == "resolved-model"
    assert resp.prompt_tokens == 11
    assert resp.completion_tokens == 7
    assert resp.finish_reason == "stop"
    # default model used when none passed; args forwarded
    assert captured["model"] == "default-model"
    assert captured["temperature"] == 0.2
    assert captured["max_tokens"] == 100
    assert captured["messages"] == [{"role": "user", "content": "hi"}]


def test_explicit_model_overrides_default():
    captured: dict = {}
    provider = OpenAICompatibleProvider(
        name="nvidia", default_model="default-model", client=_FakeClient(captured)
    )
    provider.complete([ChatMessage(role="user", content="hi")], model="override")
    assert captured["model"] == "override"
