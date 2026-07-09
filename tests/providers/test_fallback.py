import pytest

from app.providers.llm.base import ChatMessage, LLMResponse
from app.providers.llm.fallback import FallbackLLM


class _Primary:
    name = "primary"

    def __init__(self, fail_first: bool):
        self.fail_first = fail_first
        self.calls: list[str | None] = []

    def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
        self.calls.append(model)
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("primary boom")
        return LLMResponse(content="ok", model=model or "primary-model", provider="primary")


def _msgs():
    return [ChatMessage(role="user", content="hi")]


def test_passthrough_on_success():
    p = _Primary(fail_first=False)
    llm = FallbackLLM(p, fallback_model="fb-model")
    resp = llm.complete(_msgs())
    assert resp.content == "ok"
    assert p.calls == [None]  # fallback never used


def test_retries_with_fallback_model_on_failure():
    p = _Primary(fail_first=True)
    llm = FallbackLLM(p, fallback_model="fb-model")
    resp = llm.complete(_msgs())
    assert resp.content == "ok"
    assert p.calls == [None, "fb-model"]  # first default, then fallback


def test_reraises_when_no_fallback():
    p = _Primary(fail_first=True)
    llm = FallbackLLM(p, fallback_model=None)
    with pytest.raises(RuntimeError):
        llm.complete(_msgs())
