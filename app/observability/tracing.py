import os
from contextlib import contextmanager
from typing import Iterator

from app.config import Settings


def get_langfuse_client(settings: Settings):
    if not settings.langfuse_enabled:
        return None

    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key or ""
    os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key or ""
    if settings.langfuse_host:
        os.environ["LANGFUSE_HOST"] = settings.langfuse_host
        os.environ["LANGFUSE_BASE_URL"] = settings.langfuse_host

    from langfuse import get_client

    return get_client()


@contextmanager
def traced_span(client, name: str) -> Iterator[None]:
    if client is None:
        yield
        return
    with client.start_as_current_observation(as_type="span", name=name):
        yield
