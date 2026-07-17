from functools import lru_cache

from app.config import Settings
from app.observability.logging_config import configure_logging
from app.observability.tracing import get_langfuse_client


@lru_cache
def _instrument_anthropic_once() -> bool:
    from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor

    AnthropicInstrumentor().instrument()
    return True


def setup_observability(settings: Settings):
    configure_logging()
    if settings.langfuse_enabled:
        _instrument_anthropic_once()
    return get_langfuse_client(settings)
