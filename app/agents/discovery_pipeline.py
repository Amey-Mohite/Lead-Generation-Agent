import logging

from app.agents.lead_source import build_lead_source
from app.agents.orchestrator_agent import build_lead_orchestrator_agent
from app.db.repository import LeadRepository, build_lead_repository
from app.schemas.lead import Lead


def discover_and_qualify_leads(
    lead_source,
    orchestrator,
    query: str,
    max_results: int,
    repository: LeadRepository | None = None,
    skip_seen_domains: bool = False,
) -> list[Lead]:
    exclude_domains = None
    if repository is not None and skip_seen_domains:
        exclude_domains = repository.all_domains()
        logging.info(f"Discovery excluding {len(exclude_domains)} already-known domain(s)")

    candidates = lead_source.discover(query, max_results, exclude_domains=exclude_domains)
    logging.info(f"Discovery found {len(candidates)} candidate(s): {[c.domain for c in candidates]}")

    if repository is not None and skip_seen_domains:
        before = len(candidates)
        candidates = repository.filter_unseen(candidates)
        skipped = before - len(candidates)
        if skipped:
            logging.info(f"Dedup skipped {skipped} candidate(s) still already-known after exclusion")

    leads = []
    for candidate in candidates:
        try:
            lead = orchestrator.run(candidate.domain)
        except Exception:
            logging.warning(f"Skipping candidate {candidate.domain!r}: orchestrator failed", exc_info=True)
            continue

        if repository is not None:
            repository.save(lead)
        leads.append(lead)

    return leads


def run_discovery_pipeline(settings, query: str, max_results: int | None = None) -> list[Lead]:
    lead_source = build_lead_source(settings)
    orchestrator = build_lead_orchestrator_agent(settings)
    repository = build_lead_repository(settings)
    resolved_max = max_results if max_results is not None else settings.discovery_max_results
    return discover_and_qualify_leads(
        lead_source,
        orchestrator,
        query,
        resolved_max,
        repository=repository,
        skip_seen_domains=settings.discovery_skip_seen_domains,
    )


def parse_discovery_queries(discovery_queries: str) -> list[str]:
    return [q.strip() for q in discovery_queries.split(",") if q.strip()]


def run_discovery_sweep(
    settings, queries: list[str] | None = None, max_results: int | None = None
) -> list[Lead]:
    """Runs discover_and_qualify_leads once per query, reusing one repository throughout so a
    domain saved by an earlier query in the sweep is excluded from every later query too."""
    resolved_queries = queries if queries is not None else parse_discovery_queries(
        settings.discovery_queries
    )
    if not resolved_queries:
        return []

    lead_source = build_lead_source(settings)
    orchestrator = build_lead_orchestrator_agent(settings)
    repository = build_lead_repository(settings)
    resolved_max = max_results if max_results is not None else settings.discovery_max_results

    all_leads = []
    for query in resolved_queries:
        logging.info(f"Discovery sweep: running query {query!r}")
        try:
            leads = discover_and_qualify_leads(
                lead_source,
                orchestrator,
                query,
                resolved_max,
                repository=repository,
                skip_seen_domains=settings.discovery_skip_seen_domains,
            )
        except Exception:
            logging.warning(f"Skipping query {query!r}: discovery failed", exc_info=True)
            continue
        all_leads.extend(leads)

    return all_leads
