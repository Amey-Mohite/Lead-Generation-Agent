from app.agents.orchestrator_agent import LeadOrchestratorAgent, build_lead_orchestrator_agent
from app.config import Settings
from app.providers.llm.base import LLMResponse
from app.schemas.research import ResearchBrief


class _FakeResearchAgent:
    def __init__(self, brief: ResearchBrief):
        self._brief = brief

    def run(self, target: str) -> ResearchBrief:
        return self._brief


class _ScriptedLLM:
    name = "scripted"

    def __init__(self, scripts: list[str]):
        self._scripts = scripts
        self.calls: list[str] = []

    def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
        content = self._scripts[len(self.calls)]
        self.calls.append(content)
        return LLMResponse(content=content, model="scripted", provider="scripted")


def _brief() -> ResearchBrief:
    return ResearchBrief(company_name="Acme", summary="Makes widgets for other businesses.")


def test_qualified_lead_gets_a_draft():
    scripts = [
        '{"score": 85, "reasoning": "Strong B2B fit."}',
        '{"subject": "Quick question", "body": "Hi -- noticed Acme makes widgets..."}',
    ]
    agent = LeadOrchestratorAgent(
        _ScriptedLLM(scripts), _FakeResearchAgent(_brief()),
        icp_description="B2B companies", min_score_to_draft=60,
    )
    lead = agent.run("acme.com")

    assert lead.status == "qualified"
    assert lead.qualification.score == 85
    assert lead.outreach is not None
    assert lead.outreach.subject == "Quick question"


def test_disqualified_lead_skips_the_draft_call():
    scripts = ['{"score": 20, "reasoning": "Not a B2B fit."}']
    llm = _ScriptedLLM(scripts)
    agent = LeadOrchestratorAgent(
        llm, _FakeResearchAgent(_brief()),
        icp_description="B2B companies", min_score_to_draft=60,
    )
    lead = agent.run("acme.com")

    assert lead.status == "disqualified"
    assert lead.outreach is None
    assert len(llm.calls) == 1  # draft LLM call never happened


def test_icp_description_is_included_in_the_qualify_prompt():
    captured: dict = {}

    class _CapturingLLM:
        name = "capturing"

        def __init__(self):
            self._scripts = ['{"score": 90, "reasoning": "fits"}', '{"subject": "s", "body": "b"}']
            self._i = 0

        def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
            if self._i == 0:
                captured["qualify_system"] = messages[0].content
            content = self._scripts[self._i]
            self._i += 1
            return LLMResponse(content=content, model="scripted", provider="scripted")

    agent = LeadOrchestratorAgent(
        _CapturingLLM(), _FakeResearchAgent(_brief()),
        icp_description="ONLY-FINTECH-MARKER", min_score_to_draft=60,
    )
    agent.run("acme.com")
    assert "ONLY-FINTECH-MARKER" in captured["qualify_system"]


def test_build_lead_orchestrator_agent_from_settings():
    s = Settings(
        _env_file=None,
        llm_provider="openrouter",
        llm_model="test-model",
        openrouter_api_key="k",
        research_search_mode="mock",
        icp_description="Test ICP",
        icp_min_score_to_draft=70,
    )
    agent = build_lead_orchestrator_agent(s)
    assert isinstance(agent, LeadOrchestratorAgent)
    assert agent._icp_description == "Test ICP"
    assert agent._min_score_to_draft == 70
