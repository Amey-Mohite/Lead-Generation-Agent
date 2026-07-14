from app.agents.structured import complete_structured
from app.providers.llm.base import ChatMessage, LLMProvider
from app.schemas.lead import Lead, OutreachDraft, Qualification
from app.schemas.research import ResearchBrief

_QUALIFY_SYSTEM = """You are a lead qualification agent. Score how well a company fits an Ideal
Customer Profile (ICP), based on its research brief.

ICP:
{icp_description}

Respond with ONE JSON object and nothing else:
{{"score": <integer 0-100>, "reasoning": "..."}}

Rules:
- Score 0 = not a fit at all, 100 = a perfect fit.
- Base your reasoning only on the research brief provided -- do not invent facts.
- "score" and "reasoning" are both required."""

_DRAFT_SYSTEM = """You are a sales development representative drafting a first-touch outreach
email. Write a short, personalized message based on the company's research brief and why it
qualifies as a good fit.

Respond with ONE JSON object and nothing else:
{"subject": "...", "body": "..."}

Rules:
- Reference at least one specific fact from the research brief -- do not write a generic email.
- Keep the body under 6 sentences.
- Do not invent facts not present in the research brief or the qualification reasoning.
- "subject" and "body" are both required."""


class LeadOrchestratorAgent:
    def __init__(
        self,
        llm: LLMProvider,
        research_agent,
        icp_description: str,
        min_score_to_draft: int = 60,
    ) -> None:
        self._llm = llm
        self._research_agent = research_agent
        self._icp_description = icp_description
        self._min_score_to_draft = min_score_to_draft

    def run(self, target: str) -> Lead:
        brief = self._research_agent.run(target)
        qualification = self._qualify(brief)

        if qualification.score < self._min_score_to_draft:
            return Lead(
                research=brief, qualification=qualification, outreach=None, status="disqualified"
            )

        outreach = self._draft(brief, qualification)
        return Lead(
            research=brief, qualification=qualification, outreach=outreach, status="qualified"
        )

    def _qualify(self, brief: ResearchBrief) -> Qualification:
        messages = [
            ChatMessage(
                role="system",
                content=_QUALIFY_SYSTEM.format(icp_description=self._icp_description),
            ),
            ChatMessage(
                role="user", content=f"Research brief:\n{brief.model_dump_json(indent=2)}"
            ),
        ]
        result = complete_structured(self._llm, messages, Qualification)
        assert isinstance(result, Qualification)
        return result

    def _draft(self, brief: ResearchBrief, qualification: Qualification) -> OutreachDraft:
        messages = [
            ChatMessage(role="system", content=_DRAFT_SYSTEM),
            ChatMessage(
                role="user",
                content=(
                    f"Research brief:\n{brief.model_dump_json(indent=2)}\n\n"
                    f"Why this company qualifies:\n{qualification.reasoning}"
                ),
            ),
        ]
        result = complete_structured(self._llm, messages, OutreachDraft)
        assert isinstance(result, OutreachDraft)
        return result


def build_lead_orchestrator_agent(settings) -> "LeadOrchestratorAgent":
    from app.agents.research_agent import build_research_agent
    from app.providers.llm.factory import build_llm_provider
    from app.providers.llm.fallback import FallbackLLM

    research_agent = build_research_agent(settings)
    llm = FallbackLLM(build_llm_provider(settings), settings.llm_fallback_model)
    return LeadOrchestratorAgent(
        llm=llm,
        research_agent=research_agent,
        icp_description=settings.icp_description,
        min_score_to_draft=settings.icp_min_score_to_draft,
    )
