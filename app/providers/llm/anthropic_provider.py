from app.providers.llm.base import ChatMessage, LLMResponse


class AnthropicProvider:
    """LLM provider for Anthropic's native Messages API."""

    def __init__(
        self,
        *,
        default_model: str,
        api_key: str | None = None,
        client=None,
    ) -> None:
        self.name = "anthropic"
        self.default_model = default_model
        if client is not None:
            self._client = client
        else:
            from anthropic import Anthropic

            self._client = Anthropic(api_key=api_key)

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        system = "\n".join(m.content for m in messages if m.role == "system")
        conversation = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role != "system"
        ]
        resp = self._client.messages.create(
            model=model or self.default_model,
            system=system,
            messages=conversation,
            max_tokens=max_tokens or 1024,
            temperature=temperature,
            **({"tools": tools} if tools else {}),
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        return LLMResponse(
            content=text,
            model=resp.model,
            provider=self.name,
            prompt_tokens=getattr(resp.usage, "input_tokens", 0),
            completion_tokens=getattr(resp.usage, "output_tokens", 0),
            finish_reason=resp.stop_reason,
        )
