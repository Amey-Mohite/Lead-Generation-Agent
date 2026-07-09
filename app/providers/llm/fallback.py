from app.providers.llm.base import ChatMessage, LLMProvider, LLMResponse


class FallbackLLM:
    """Wraps a provider; retries once on the fallback model if the primary call fails."""

    def __init__(self, primary: LLMProvider, fallback_model: str | None) -> None:
        self._primary = primary
        self._fallback_model = fallback_model
        self.name = f"{primary.name}+fallback"

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        try:
            return self._primary.complete(
                messages, model=model, temperature=temperature, max_tokens=max_tokens
            )
        except Exception:
            if self._fallback_model is None:
                raise
            return self._primary.complete(
                messages,
                model=self._fallback_model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
