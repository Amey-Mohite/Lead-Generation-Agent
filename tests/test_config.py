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


def test_discovery_max_results_default():
    s = Settings(_env_file=None)
    assert s.discovery_max_results == 20


def test_discovery_max_results_env_override(monkeypatch):
    monkeypatch.setenv("DISCOVERY_MAX_RESULTS", "5")
    s = Settings(_env_file=None)
    assert s.discovery_max_results == 5


def test_lead_search_defaults():
    s = Settings(_env_file=None)
    assert s.lead_search_mode == "native"
    assert s.lead_search_provider == "tavily"
    assert s.lead_search_api_key is None


def test_lead_search_env_override(monkeypatch):
    monkeypatch.setenv("LEAD_SEARCH_MODE", "api")
    monkeypatch.setenv("LEAD_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("LEAD_SEARCH_API_KEY", "tvly-test-key")
    s = Settings(_env_file=None)
    assert s.lead_search_mode == "api"
    assert s.lead_search_provider == "tavily"
    assert s.lead_search_api_key == "tvly-test-key"


def test_company_description_default():
    s = Settings(_env_file=None)
    assert len(s.company_description) > 0


def test_company_description_env_override(monkeypatch):
    monkeypatch.setenv("COMPANY_DESCRIPTION", "Acme Corp sells widgets.")
    s = Settings(_env_file=None)
    assert s.company_description == "Acme Corp sells widgets."


def test_discovery_skip_seen_domains_default():
    s = Settings(_env_file=None)
    assert s.discovery_skip_seen_domains is True


def test_discovery_skip_seen_domains_env_override(monkeypatch):
    monkeypatch.setenv("DISCOVERY_SKIP_SEEN_DOMAINS", "false")
    s = Settings(_env_file=None)
    assert s.discovery_skip_seen_domains is False


def test_discovery_queries_default_is_empty():
    s = Settings(_env_file=None)
    assert s.discovery_queries == ""


def test_discovery_queries_env_override(monkeypatch):
    monkeypatch.setenv("DISCOVERY_QUERIES", "credit unions in the UK,building societies UK")
    s = Settings(_env_file=None)
    assert s.discovery_queries == "credit unions in the UK,building societies UK"
