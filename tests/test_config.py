from app.config import Settings, get_settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.app_name == "lead-gen-agent"
    assert s.llm_provider == "openrouter"
    assert s.research_search_mode == "native"
    assert s.exporters == "excel"
    assert s.langfuse_enabled is False


def test_env_override(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("RESEARCH_SEARCH_MODE", "mock")
    s = Settings(_env_file=None)
    assert s.llm_provider == "anthropic"
    assert s.research_search_mode == "mock"


def test_get_settings_is_cached():
    get_settings.cache_clear()
    assert get_settings() is get_settings()
