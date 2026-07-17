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
        langfuse_enabled: bool = False,
    ) -> None:
        self.name = name
        self.default_model = default_model
        if client is not None:
            self._client = client
        elif langfuse_enabled:
            from langfuse.openai import OpenAI
            self._client = OpenAI(base_url=base_url, api_key=api_key)
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

    def complete_native_search(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Native web search via OpenAI's Responses API (openai provider only).

        This is a different endpoint than complete()'s Chat Completions call --
        OpenAI's server-side web_search tool is only available on Responses.
        """
        if self.name != "openai":
            raise ValueError(
                f"Native web search via the Responses API is only supported for "
                f"provider 'openai', not {self.name!r}. Use RESEARCH_SEARCH_MODE=api instead."
            )
        instructions = "\n".join(m.content for m in messages if m.role == "system") or None
        input_messages = [
            {"role": m.role, "content": m.content} for m in messages if m.role != "system"
        ]
        resp = self._client.responses.create(
            model=model or self.default_model,
            instructions=instructions,
            input=input_messages,
            tools=[{"type": "web_search"}],
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        usage = resp.usage
        return LLMResponse(
            content=resp.output_text,
            model=resp.model,
            provider=self.name,
            prompt_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
            finish_reason=getattr(resp, "status", None),
        )
