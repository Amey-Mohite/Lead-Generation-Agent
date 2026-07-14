import pytest
from pydantic import BaseModel

from app.agents.structured import StructuredOutputError, complete_structured
from app.providers.llm.base import ChatMessage, LLMResponse


class _Widget(BaseModel):
    name: str
    count: int


class _ScriptedLLM:
    name = "scripted"

    def __init__(self, scripts: list[str]):
        self._scripts = scripts
        self.calls = 0

    def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
        content = self._scripts[self.calls]
        self.calls += 1
        return LLMResponse(content=content, model="scripted", provider="scripted")


def _msgs():
    return [ChatMessage(role="user", content="describe a widget")]


def test_happy_path_parses_first_reply():
    llm = _ScriptedLLM(['{"name": "gadget", "count": 3}'])
    result = complete_structured(llm, _msgs(), _Widget)
    assert result == _Widget(name="gadget", count=3)


def test_recovers_from_non_json_reply():
    llm = _ScriptedLLM(["not json at all", '{"name": "gadget", "count": 3}'])
    result = complete_structured(llm, _msgs(), _Widget)
    assert result == _Widget(name="gadget", count=3)
    assert llm.calls == 2


def test_recovers_from_validation_error():
    llm = _ScriptedLLM(['{"name": "gadget"}', '{"name": "gadget", "count": 3}'])
    result = complete_structured(llm, _msgs(), _Widget)
    assert result == _Widget(name="gadget", count=3)


def test_raises_after_max_retries():
    llm = _ScriptedLLM(["nope", "still nope", "nope again"])
    with pytest.raises(StructuredOutputError):
        complete_structured(llm, _msgs(), _Widget, max_retries=3)
