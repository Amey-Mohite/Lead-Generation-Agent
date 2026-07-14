from app.agents.lead_source import build_lead_source
from app.agents.orchestrator_agent import build_lead_orchestrator_agent
from app.schemas.lead import Lead


def discover_and_qualify_leads(lead_source, orchestrator, query: str, max_results: int) -> list[Lead]:
    candidates = lead_source.discover(query, max_results)
    return [orchestrator.run(candidate.domain) for candidate in candidates]


def run_discovery_pipeline(settings, query: str, max_results: int | None = None) -> list[Lead]:
    lead_source = build_lead_source(settings)
    orchestrator = build_lead_orchestrator_agent(settings)
    resolved_max = max_results if max_results is not None else settings.discovery_max_results
    return discover_and_qualify_leads(lead_source, orchestrator, query, resolved_max)
