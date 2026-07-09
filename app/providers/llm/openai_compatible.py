from app.providers.llm.base import ChatMessage, LLMResponse


class OpenAICompatibleProvider:
    """LLM provider for any OpenAI-compatible API (OpenRouter, NVIDIA, OpenAI)."""

    def __init__(
        self,
        *,
        name: str,
        default_model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        client=None,
    ) -> None:
        self.name = name
        self.default_model = default_model
        if client is not None:
            self._client = client
        else:
            from openai import OpenAI

            self._client = OpenAI(base_url=base_url, api_key=api_key)

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        resp = self._client.chat.completions.create(
            model=model or self.default_model,
            messages=[m.model_dump() for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        choice = resp.choices[0]
        usage = resp.usage
        return LLMResponse(
            content=choice.message.content or "",
            model=resp.model,
            provider=self.name,
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
            finish_reason=choice.finish_reason,
        )
