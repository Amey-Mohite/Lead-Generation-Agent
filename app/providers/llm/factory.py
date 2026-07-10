from app.config import Settings
from app.providers.llm.anthropic_provider import AnthropicProvider
from app.providers.llm.base import LLMProvider
from app.providers.llm.openai_compatible import OpenAICompatibleProvider
import logging

logging.basicConfig(level=logging.INFO)


_OPENAI_COMPATIBLE = {
    "openrouter": ("https://openrouter.ai/api/v1", "openrouter_api_key"),
    "nvidia": ("https://integrate.api.nvidia.com/v1", "nvidia_api_key"),
    "openai": (None, "openai_api_key"),
}


def build_llm_provider(settings: Settings) -> LLMProvider:
    provider = settings.llm_provider.lower()
    logging.info(f"Building LLM provider {provider} with model={settings.llm_model}")
    if provider in _OPENAI_COMPATIBLE:
        base_url, key_attr = _OPENAI_COMPATIBLE[provider]
        logging.info(f"Building OpenAI-compatible provider {provider} with base_url={base_url} and key_attr={key_attr}")
        return OpenAICompatibleProvider(
            name=provider,
            default_model=settings.llm_model,
            base_url=base_url,
            api_key=getattr(settings, key_attr),
        )

    if provider == "anthropic":
        return AnthropicProvider(
            default_model=settings.llm_model,
            api_key=settings.anthropic_api_key,
        )

    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")
