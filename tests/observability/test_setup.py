from app.config import Settings
from app.observability.setup import setup_observability


def test_setup_observability_returns_none_when_langfuse_disabled():
    settings = Settings(_env_file=None, langfuse_enabled=False)
    client = setup_observability(settings)
    assert client is None
