from app.providers.llm.base import ChatMessage, LLMProvider, LLMResponse


def test_chat_message_roundtrip():
    m = ChatMessage(role="user", content="hi")
    assert m.model_dump() == {"role": "user", "content": "hi"}


def test_llm_response_defaults():
    r = LLMResponse(content="hello", model="m", provider="p")
    assert r.prompt_tokens == 0
    assert r.completion_tokens == 0
    assert r.finish_reason is None


def test_protocol_is_runtime_checkable():
    class Dummy:
        name = "dummy"

        def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
            return LLMResponse(content="x", model="m", provider="dummy")

    assert isinstance(Dummy(), LLMProvider)
