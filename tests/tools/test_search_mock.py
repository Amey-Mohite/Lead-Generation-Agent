from app.config import Settings
from app.tools.search.base import SearchResult
from app.tools.search.factory import build_search_backend
from app.tools.search.mock import MockSearchBackend


def test_mock_returns_k_results():
    backend = MockSearchBackend()
    results = backend.search("acme corp", k=3)
    assert len(results) == 3
    assert all(isinstance(r, SearchResult) for r in results)
    assert "acme corp" in results[0].snippet.lower()


def test_factory_defaults_to_mock():
    s = Settings(_env_file=None, research_search_mode="mock")
    assert isinstance(build_search_backend(s), MockSearchBackend)


def test_factory_native_falls_back_to_mock():
    s = Settings(_env_file=None, research_search_mode="native")
    assert isinstance(build_search_backend(s), MockSearchBackend)
