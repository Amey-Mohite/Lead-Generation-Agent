from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # App
    app_name: str = "lead-gen-agent"
    app_version: str = "0.1.0"
    environment: str = "development"

    # LLM (swap provider with one line)
    llm_provider: str = "openrouter"          # openrouter|nvidia|openai|anthropic|local
    llm_model: str = "anthropic/claude-sonnet-5"
    llm_fallback_model: str | None = None
    openrouter_api_key: str | None = None
    nvidia_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # Research / search (default: native = model does its own web search)
    research_search_mode: str = "native"      # native|api|mock
    search_provider: str = "tavily"           # tavily|serpapi|brave
    search_api_key: str | None = None

    # Lead qualification (ICP = Ideal Customer Profile)
    icp_description: str = (
        "A B2B software or technology company with 10-500 employees, "
        "based in North America or Europe."
    )
    icp_min_score_to_draft: int = 60

    # Our own company/offering -- feeds the outreach draft step so emails pitch something
    # concrete, rather than just knowing who the target is (that's what ICP_DESCRIPTION is for).
    company_description: str = (
        "Our company builds a product our prospective customers would benefit from."
    )

    # Discovery (broad-query enumeration -> many candidate companies)
    discovery_max_results: int = 20
    # Discovery dedup -- once a domain is in the leads table, skip re-processing it (permanent)
    discovery_skip_seen_domains: bool = True
    # Optional comma list of queries to sweep through in one run_discovery_sweep() call,
    # e.g. "credit unions in the UK,building societies UK,SME lenders UK open banking"
    discovery_queries: str = ""
    # Discovery's own search config -- independent of RESEARCH_SEARCH_MODE, since Discovery may
    # need a different search strategy than the Research Agent (e.g. Research in "api" mode while
    # Discovery uses "native", or vice versa).
    lead_search_mode: str = "native"          # native|api|mock
    lead_search_provider: str = "tavily"      # tavily|serpapi|brave
    lead_search_api_key: str | None = None

    # Outputs
    exporters: str = "excel"                  # comma list: excel,slack,email,gmail
    export_dir: str = "./out/leads"
    slack_webhook_url: str | None = None
    smtp_url: str | None = None
    gmail_credentials: str | None = None

    # Observability
    langfuse_enabled: bool = True
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None
    n8n_alert_webhook_url: str | None = None

    # Infra / API
    database_url: str = "postgresql+psycopg://postgres:password@localhost:5432/leadgen"
    api_key: str | None = None
    rate_limit_per_min: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
