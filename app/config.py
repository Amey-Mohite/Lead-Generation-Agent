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

    # Outputs
    exporters: str = "excel"                  # comma list: excel,slack,email,gmail
    export_dir: str = "./out/leads"
    slack_webhook_url: str | None = None
    smtp_url: str | None = None
    gmail_credentials: str | None = None

    # Observability
    langfuse_enabled: bool = False
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None

    # Infra / API
    database_url: str = "postgresql+psycopg://leads:leads@localhost:5432/leads"
    api_key: str | None = None
    rate_limit_per_min: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
