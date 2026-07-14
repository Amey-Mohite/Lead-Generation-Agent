"""Manual end-to-end check of the Lead Orchestrator Agent (research -> qualify -> draft).

Usage:
    ./.venv/Scripts/python.exe scripts/try_lead.py [target] [--demo]

Behaviour:
- If an API key for the configured LLM_PROVIDER is present, does a REAL run: real research
  (respecting RESEARCH_SEARCH_MODE), real qualification against ICP_DESCRIPTION, and a real
  outreach draft if the score clears ICP_MIN_SCORE_TO_DRAFT.
- If no key is found (or --demo is passed), runs an OFFLINE scripted demo (no network, no keys).
"""

import sys

from app.agents.orchestrator_agent import LeadOrchestratorAgent
from app.config import get_settings
from app.providers.llm.base import LLMResponse
from app.schemas.research import ResearchBrief

_KEY_ATTR = {
    "openrouter": "openrouter_api_key",
    "nvidia": "nvidia_api_key",
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
}


class _ScriptedResearchAgent:
    def __init__(self, target: str) -> None:
        self._target = target

    def run(self, target: str) -> ResearchBrief:
        return ResearchBrief(
            company_name=target,
            domain=target,
            industry="(demo)",
            summary=f"Offline demo brief for {target}.",
            key_facts=["This is a scripted demo, not real research."],
            sources=["https://example.com"],
        )


class _ScriptedLLM:
    name = "scripted-demo"

    def __init__(self) -> None:
        self._scripts = [
            '{"score": 82, "reasoning": '
            '"Demo company matches the ICP closely enough for this scripted run."}',
            '{"subject": "Quick question for you", '
            '"body": "Hi there -- noticed your work and wanted to reach out. '
            '(This is a scripted demo body.)"}',
        ]
        self._i = 0

    def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
        content = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return LLMResponse(content=content, model="scripted", provider="scripted")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = [a for a in sys.argv[1:] if a != "--demo"]
    force_demo = "--demo" in sys.argv
    target = args[0] if args else "stripe.com"

    settings = get_settings()
    key_attr = _KEY_ATTR.get(settings.llm_provider)
    has_key = bool(getattr(settings, key_attr, None)) if key_attr else False

    if has_key and not force_demo:
        from app.agents.orchestrator_agent import build_lead_orchestrator_agent

        print(
            f"[REAL run] provider={settings.llm_provider} model={settings.llm_model} "
            f"search_mode={settings.research_search_mode} target={target}"
        )
        print(f"ICP: {settings.icp_description}")
        print(f"Company: {settings.company_description}")
        print(f"Min score to draft: {settings.icp_min_score_to_draft}")
        agent = build_lead_orchestrator_agent(settings)
    else:
        why = "forced --demo" if force_demo else f"no API key for '{settings.llm_provider}'"
        print(
            f"[OFFLINE demo] ({why}) target={target}\n"
            f"  -> set a key in .env (e.g. OPENROUTER_API_KEY) for a real run."
        )
        agent = LeadOrchestratorAgent(
            _ScriptedLLM(),
            _ScriptedResearchAgent(target),
            icp_description="Any company (demo mode)",
            company_description="A demo company (demo mode).",
            min_score_to_draft=60,
        )

    lead = agent.run(target)

    print("\n================ LEAD ================")
    print(lead.model_dump_json(indent=2))

    if has_key and not force_demo:
        from app.db.repository import build_lead_repository

        build_lead_repository(settings).save(lead)
        print("\nPersisted to Postgres.")

    from app.exporters.factory import build_exporters

    for exporter in build_exporters(settings):
        path = exporter.export([lead])
        print(f"\nExported to: {path}")


if __name__ == "__main__":
    main()
