from app.agents.research_agent import ResearchAgent, build_research_agent
from app.config import Settings


def test_build_research_agent_from_settings():
    s = Settings(
        _env_file=None,
        llm_provider="openrouter",
        llm_model="test-model",
        openrouter_api_key="k",
        research_search_mode="mock",
    )
    agent = build_research_agent(s)
    assert isinstance(agent, ResearchAgent)
    assert "web_search" in agent._registry.describe()


def test_build_research_agent_native_mode_uses_online_llm_no_web_search_tool():
    s = Settings(
        _env_file=None,
        llm_provider="openrouter",
        llm_model="test-model",
        openrouter_api_key="k",
        research_search_mode="native",
    )
    agent = build_research_agent(s)
    assert isinstance(agent, ResearchAgent)
    assert "online" in agent._llm.name
    assert "web_search" not in agent._registry.describe()
    assert "fetch_url" in agent._registry.describe()


def test_build_research_agent_native_mode_works_for_anthropic_too():
    s = Settings(
        _env_file=None,
        llm_provider="anthropic",
        llm_model="claude-test-model",
        anthropic_api_key="k",
        research_search_mode="native",
    )
    agent = build_research_agent(s)
    assert isinstance(agent, ResearchAgent)
    assert "online" in agent._llm.name
    assert "web_search" not in agent._registry.describe()


def test_build_research_agent_native_mode_works_for_openai_too():
    s = Settings(
        _env_file=None,
        llm_provider="openai",
        llm_model="gpt-test-model",
        openai_api_key="k",
        research_search_mode="native",
    )
    agent = build_research_agent(s)
    assert isinstance(agent, ResearchAgent)
    assert "online" in agent._llm.name
    assert "web_search" not in agent._registry.describe()
