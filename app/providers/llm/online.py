from app.providers.llm.base import ChatMessage, LLMProvider, LLMResponse

_SUPPORTED_PROVIDERS = {"openrouter", "anthropic", "openai"}

# Anthropic's server-side web search tool -- runs on Anthropic's infrastructure,
# needs only ANTHROPIC_API_KEY. https://platform.claude.com (Server Tools).
_ANTHROPIC_WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search"}


class OnlineSearchLLM:
    """Wraps an LLMProvider to request the provider's built-in ("native") web search.

    Each vendor implements this differently, so this class dispatches by provider name
    rather than using one mechanism for all:

    - openrouter: appending ':online' to the model id makes OpenRouter run live web
      search and ground the answer, with no separate search API key required.
    - anthropic: a server-side `web_search` tool declared in the `tools` param of the
      Messages API -- runs on Anthropic's infrastructure, needs only ANTHROPIC_API_KEY.
    - openai: a server-side `web_search` tool, but ONLY on the Responses API (not Chat
      Completions) -- dispatched to OpenAICompatibleProvider.complete_native_search().

    NVIDIA NIM has no native/server-side search offering (verified): it only exposes
    generic OpenAI-compatible tool-calling, so RESEARCH_SEARCH_MODE=api is required there.
    """

    def __init__(self, primary: LLMProvider) -> None:
        if primary.name not in _SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Native web search is not supported for provider {primary.name!r}. "
                f"Supported providers: {sorted(_SUPPORTED_PROVIDERS)}. "
                "Use RESEARCH_SEARCH_MODE=api (e.g. with Tavily) instead."
            )
        self._primary = primary
        self.name = f"{primary.name}+online"

    @staticmethod
    def _online_model(model: str) -> str:
        return model if model.endswith(":online") else f"{model}:online"

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        if self._primary.name == "openrouter":
            base_model = model or getattr(self._primary, "default_model", None)
            if not base_model:
                raise ValueError(
                    "OnlineSearchLLM needs an explicit model or a provider with "
                    "default_model set."
                )
            return self._primary.complete(
                messages,
                model=self._online_model(base_model),
                temperature=temperature,
                max_tokens=max_tokens,
            )

        if self._primary.name == "anthropic":
            return self._primary.complete(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=[_ANTHROPIC_WEB_SEARCH_TOOL],
            )

        # self._primary.name == "openai" (only remaining supported provider)
        return self._primary.complete_native_search(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
