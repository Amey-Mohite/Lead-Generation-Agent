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


def test_icp_defaults():
    s = Settings(_env_file=None)
    assert "B2B" in s.icp_description or len(s.icp_description) > 0
    assert s.icp_min_score_to_draft == 60


def test_icp_env_override(monkeypatch):
    monkeypatch.setenv("ICP_DESCRIPTION", "Only fintech startups under 50 employees.")
    monkeypatch.setenv("ICP_MIN_SCORE_TO_DRAFT", "80")
    s = Settings(_env_file=None)
    assert s.icp_description == "Only fintech startups under 50 employees."
    assert s.icp_min_score_to_draft == 80
