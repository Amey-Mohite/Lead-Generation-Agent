"""Manual end-to-end check of the Discovery layer (query -> candidates -> N leads).

Usage:
    ./.venv/Scripts/python.exe scripts/try_discovery.py ["credit unions in the UK"] [--demo]

Behaviour:
- If an API key for the configured LLM_PROVIDER is present, does a REAL run: real discovery
  (respecting RESEARCH_SEARCH_MODE + DISCOVERY_MAX_RESULTS), then real research/qualify/draft for
  every discovered candidate.
- If no key is found (or --demo is passed), runs an OFFLINE scripted demo (no network, no keys)
  with two canned candidates.
"""

import sys

from app.config import get_settings
from app.schemas.discovery import Candidate
from app.schemas.lead import Lead, Qualification
from app.schemas.research import ResearchBrief

_KEY_ATTR = {
    "openrouter": "openrouter_api_key",
    "nvidia": "nvidia_api_key",
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
}


class _ScriptedLeadSource:
    def discover(self, query: str, max_results: int) -> list[Candidate]:
        demo = [
            Candidate(name="Acme Credit Union", domain="acme-cu-demo.example"),
            Candidate(name="Beta Credit Union", domain="beta-cu-demo.example"),
        ]
        return demo[:max_results]


class _ScriptedOrchestrator:
    def run(self, target: str) -> Lead:
        return Lead(
            research=ResearchBrief(
                company_name=target,
                domain=target,
                industry="(demo)",
                summary=f"Offline demo brief for {target}.",
                key_facts=["This is a scripted demo, not real research."],
                sources=["https://example.com"],
            ),
            qualification=Qualification(
                score=82, reasoning="Demo candidate matches the ICP closely enough for this run."
            ),
            outreach=None,
            status="qualified",
        )


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--demo"]
    force_demo = "--demo" in sys.argv
    query = args[0] if args else "credit unions in the UK"

    settings = get_settings()
    key_attr = _KEY_ATTR.get(settings.llm_provider)
    has_key = bool(getattr(settings, key_attr, None)) if key_attr else False

    if has_key and not force_demo:
        from app.agents.discovery_pipeline import run_discovery_pipeline

        print(
            f"[REAL run] provider={settings.llm_provider} model={settings.llm_model} "
            f"search_mode={settings.research_search_mode} query={query!r} "
            f"max_results={settings.discovery_max_results}"
        )
        print(f"ICP: {settings.icp_description}")
        leads = run_discovery_pipeline(settings, query)
    else:
        why = "forced --demo" if force_demo else f"no API key for '{settings.llm_provider}'"
        print(
            f"[OFFLINE demo] ({why}) query={query!r}\n"
            f"  -> set a key in .env (e.g. OPENROUTER_API_KEY) for a real run."
        )
        from app.agents.discovery_pipeline import discover_and_qualify_leads

        leads = discover_and_qualify_leads(
            _ScriptedLeadSource(), _ScriptedOrchestrator(), query, max_results=2
        )

    print(f"\n================ {len(leads)} LEAD(S) FOUND ================")
    for i, lead in enumerate(leads, start=1):
        print(f"\n--- Lead {i}: {lead.research.company_name} ---")
        print(lead.model_dump_json(indent=2))

    from app.exporters.factory import build_exporters

    for exporter in build_exporters(settings):
        path = exporter.export(leads)
        print(f"\nExported {len(leads)} lead(s) to: {path}")


if __name__ == "__main__":
    main()
