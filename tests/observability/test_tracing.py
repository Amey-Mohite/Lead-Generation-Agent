from app.config import Settings
from app.observability.tracing import get_langfuse_client, traced_span


def test_get_langfuse_client_returns_none_when_disabled():
    settings = Settings(_env_file=None, langfuse_enabled=False)
    assert get_langfuse_client(settings) is None


def test_traced_span_is_a_noop_when_client_is_none():
    executed = False
    with traced_span(None, "test-span"):
        executed = True
    assert executed is True


def test_traced_span_delegates_to_client_when_present():
    calls = []

    class _FakeObservation:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class _FakeClient:
        def start_as_current_observation(self, *, as_type, name):
            calls.append((as_type, name))
            return _FakeObservation()

    with traced_span(_FakeClient(), "research"):
        pass

    assert calls == [("span", "research")]
