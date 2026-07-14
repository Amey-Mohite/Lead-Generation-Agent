from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.agents.discovery_pipeline as discovery_pipeline_module
from app.agents.discovery_pipeline import (
    discover_and_qualify_leads,
    parse_discovery_queries,
    run_discovery_pipeline,
    run_discovery_sweep,
)
from app.config import Settings
from app.db.models import Base, LeadRecord
from app.db.repository import LeadRepository
from app.schemas.discovery import Candidate
from app.schemas.lead import Lead, Qualification
from app.schemas.research import ResearchBrief


def _lead_for(target: str) -> Lead:
    return Lead(
        research=ResearchBrief(company_name=target, domain=target, summary=f"Summary for {target}"),
        qualification=Qualification(score=80, reasoning="ok"),
        status="qualified",
    )


class _FakeLeadSource:
    def __init__(self, candidates: list[Candidate]):
        self._candidates = candidates
        self.exclude_domains_seen: list[str] | None = "not called"

    def discover(
        self, query: str, max_results: int, exclude_domains: list[str] | None = None
    ) -> list[Candidate]:
        self.exclude_domains_seen = exclude_domains
        return self._candidates[:max_results]


class _FakeOrchestrator:
    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.targets_seen: list[str] = []
        self._fail_on = fail_on or set()

    def run(self, target: str) -> Lead:
        self.targets_seen.append(target)
        if target in self._fail_on:
            raise RuntimeError(f"simulated orchestrator failure for {target}")
        return _lead_for(target)


def _in_memory_repository():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return LeadRepository(sessionmaker(bind=engine)), sessionmaker(bind=engine)


def _seed(session_factory, domain: str) -> None:
    with session_factory() as session:
        now = datetime.now(timezone.utc)
        session.add(
            LeadRecord(
                domain=domain, company_name="Acme", status="qualified", score=80,
                reasoning="ok", summary="s", key_facts=[], contacts=[], sources=[],
                first_seen_at=now, last_seen_at=now,
            )
        )
        session.commit()


def test_discover_and_qualify_runs_each_candidate_through_the_orchestrator():
    candidates = [
        Candidate(name="Acme", domain="acme.com"),
        Candidate(name="Beta", domain="beta.com"),
    ]
    source = _FakeLeadSource(candidates)
    orchestrator = _FakeOrchestrator()

    leads = discover_and_qualify_leads(source, orchestrator, "credit unions", max_results=2)

    assert len(leads) == 2
    assert orchestrator.targets_seen == ["acme.com", "beta.com"]
    assert all(isinstance(lead, Lead) for lead in leads)


def test_run_discovery_pipeline_uses_settings_default_max_results(monkeypatch):
    fake_source = _FakeLeadSource([Candidate(name="Acme", domain="acme.com")] * 5)
    fake_orchestrator = _FakeOrchestrator()
    fake_repo, _ = _in_memory_repository()
    monkeypatch.setattr(discovery_pipeline_module, "build_lead_source", lambda settings: fake_source)
    monkeypatch.setattr(
        discovery_pipeline_module, "build_lead_orchestrator_agent", lambda settings: fake_orchestrator
    )
    monkeypatch.setattr(discovery_pipeline_module, "build_lead_repository", lambda settings: fake_repo)

    s = Settings(_env_file=None, discovery_max_results=3)
    leads = run_discovery_pipeline(s, "credit unions")

    assert len(leads) == 3


def test_run_discovery_pipeline_explicit_max_results_overrides_settings(monkeypatch):
    fake_source = _FakeLeadSource([Candidate(name="Acme", domain="acme.com")] * 5)
    fake_orchestrator = _FakeOrchestrator()
    fake_repo, _ = _in_memory_repository()
    monkeypatch.setattr(discovery_pipeline_module, "build_lead_source", lambda settings: fake_source)
    monkeypatch.setattr(
        discovery_pipeline_module, "build_lead_orchestrator_agent", lambda settings: fake_orchestrator
    )
    monkeypatch.setattr(discovery_pipeline_module, "build_lead_repository", lambda settings: fake_repo)

    s = Settings(_env_file=None, discovery_max_results=3)
    leads = run_discovery_pipeline(s, "credit unions", max_results=1)

    assert len(leads) == 1


def test_discover_and_qualify_skips_previously_seen_domains_when_enabled():
    repo, session_factory = _in_memory_repository()
    _seed(session_factory, "acme.com")

    candidates = [Candidate(name="Acme", domain="acme.com"), Candidate(name="Beta", domain="beta.com")]
    source = _FakeLeadSource(candidates)
    orchestrator = _FakeOrchestrator()

    leads = discover_and_qualify_leads(
        source, orchestrator, "credit unions", max_results=2,
        repository=repo, skip_seen_domains=True,
    )

    assert orchestrator.targets_seen == ["beta.com"]
    assert len(leads) == 1


def test_discover_and_qualify_ignores_dedup_when_disabled():
    repo, session_factory = _in_memory_repository()
    _seed(session_factory, "acme.com")

    candidates = [Candidate(name="Acme", domain="acme.com")]
    source = _FakeLeadSource(candidates)
    orchestrator = _FakeOrchestrator()

    leads = discover_and_qualify_leads(
        source, orchestrator, "credit unions", max_results=1,
        repository=repo, skip_seen_domains=False,
    )

    assert orchestrator.targets_seen == ["acme.com"]
    assert len(leads) == 1


def test_discover_and_qualify_persists_every_processed_lead():
    repo, session_factory = _in_memory_repository()
    candidates = [Candidate(name="Acme", domain="acme.com"), Candidate(name="Beta", domain="beta.com")]
    source = _FakeLeadSource(candidates)
    orchestrator = _FakeOrchestrator()

    discover_and_qualify_leads(
        source, orchestrator, "credit unions", max_results=2, repository=repo,
    )

    with session_factory() as session:
        assert session.query(LeadRecord).count() == 2


def test_discover_and_qualify_skips_a_failing_candidate_but_keeps_the_rest():
    candidates = [
        Candidate(name="Acme", domain="acme.com"),
        Candidate(name="Broken", domain="broken.com"),
        Candidate(name="Beta", domain="beta.com"),
    ]
    source = _FakeLeadSource(candidates)
    orchestrator = _FakeOrchestrator(fail_on={"broken.com"})

    leads = discover_and_qualify_leads(source, orchestrator, "credit unions", max_results=3)

    assert orchestrator.targets_seen == ["acme.com", "broken.com", "beta.com"]
    assert [lead.research.domain for lead in leads] == ["acme.com", "beta.com"]


def test_discover_and_qualify_persists_leads_processed_before_a_later_failure():
    repo, session_factory = _in_memory_repository()
    candidates = [
        Candidate(name="Acme", domain="acme.com"),
        Candidate(name="Beta", domain="beta.com"),
        Candidate(name="Broken", domain="broken.com"),
    ]
    source = _FakeLeadSource(candidates)
    orchestrator = _FakeOrchestrator(fail_on={"broken.com"})

    leads = discover_and_qualify_leads(
        source, orchestrator, "credit unions", max_results=3, repository=repo,
    )

    assert len(leads) == 2
    with session_factory() as session:
        saved_domains = {r.domain for r in session.query(LeadRecord).all()}
    assert saved_domains == {"acme.com", "beta.com"}


def test_discover_and_qualify_passes_known_domains_as_excludes_when_dedup_enabled():
    repo, session_factory = _in_memory_repository()
    _seed(session_factory, "acme.com")

    source = _FakeLeadSource([Candidate(name="Beta", domain="beta.com")])
    orchestrator = _FakeOrchestrator()

    discover_and_qualify_leads(
        source, orchestrator, "credit unions", max_results=1,
        repository=repo, skip_seen_domains=True,
    )

    assert source.exclude_domains_seen == ["acme.com"]


def test_discover_and_qualify_passes_no_excludes_when_dedup_disabled():
    repo, session_factory = _in_memory_repository()
    _seed(session_factory, "acme.com")

    source = _FakeLeadSource([Candidate(name="Acme", domain="acme.com")])
    orchestrator = _FakeOrchestrator()

    discover_and_qualify_leads(
        source, orchestrator, "credit unions", max_results=1,
        repository=repo, skip_seen_domains=False,
    )

    assert source.exclude_domains_seen is None


def test_discover_and_qualify_passes_no_excludes_without_a_repository():
    source = _FakeLeadSource([Candidate(name="Acme", domain="acme.com")])
    orchestrator = _FakeOrchestrator()

    discover_and_qualify_leads(source, orchestrator, "credit unions", max_results=1)

    assert source.exclude_domains_seen is None


def test_run_discovery_pipeline_wires_a_real_repository(monkeypatch):
    fake_source = _FakeLeadSource([Candidate(name="Acme", domain="acme.com")])
    fake_orchestrator = _FakeOrchestrator()
    repo, _ = _in_memory_repository()

    monkeypatch.setattr(discovery_pipeline_module, "build_lead_source", lambda settings: fake_source)
    monkeypatch.setattr(
        discovery_pipeline_module, "build_lead_orchestrator_agent", lambda settings: fake_orchestrator
    )
    monkeypatch.setattr(discovery_pipeline_module, "build_lead_repository", lambda settings: repo)

    s = Settings(_env_file=None, discovery_max_results=1)
    leads = run_discovery_pipeline(s, "credit unions")

    assert len(leads) == 1
    assert fake_orchestrator.targets_seen == ["acme.com"]


def test_parse_discovery_queries_splits_trims_and_drops_empties():
    assert parse_discovery_queries("credit unions in the UK, building societies UK ,,") == [
        "credit unions in the UK",
        "building societies UK",
    ]


def test_parse_discovery_queries_empty_string_returns_empty_list():
    assert parse_discovery_queries("") == []


class _FakeMultiQueryLeadSource:
    def __init__(self, candidates_by_query: dict[str, list[Candidate]], fail_on_query: str | None = None):
        self._by_query = candidates_by_query
        self._fail_on_query = fail_on_query

    def discover(
        self, query: str, max_results: int, exclude_domains: list[str] | None = None
    ) -> list[Candidate]:
        if query == self._fail_on_query:
            raise RuntimeError(f"simulated discovery failure for query {query!r}")
        return self._by_query.get(query, [])[:max_results]


def test_run_discovery_sweep_runs_every_query_and_dedups_across_them(monkeypatch):
    source = _FakeMultiQueryLeadSource(
        {
            "query one": [Candidate(name="Acme", domain="acme.com"), Candidate(name="Beta", domain="beta.com")],
            "query two": [Candidate(name="Beta", domain="beta.com"), Candidate(name="Gamma", domain="gamma.com")],
        }
    )
    orchestrator = _FakeOrchestrator()
    repo, _ = _in_memory_repository()

    monkeypatch.setattr(discovery_pipeline_module, "build_lead_source", lambda settings: source)
    monkeypatch.setattr(
        discovery_pipeline_module, "build_lead_orchestrator_agent", lambda settings: orchestrator
    )
    monkeypatch.setattr(discovery_pipeline_module, "build_lead_repository", lambda settings: repo)

    s = Settings(_env_file=None, discovery_max_results=5)
    leads = run_discovery_sweep(s, queries=["query one", "query two"])

    # beta.com discovered by query one is already saved by the time query two runs,
    # so it must not be processed a second time even though query two "found" it again.
    assert orchestrator.targets_seen == ["acme.com", "beta.com", "gamma.com"]
    assert {lead.research.domain for lead in leads} == {"acme.com", "beta.com", "gamma.com"}


def test_run_discovery_sweep_skips_a_failing_query_but_keeps_the_rest(monkeypatch):
    source = _FakeMultiQueryLeadSource(
        {
            "query one": [Candidate(name="Acme", domain="acme.com")],
            "query three": [Candidate(name="Gamma", domain="gamma.com")],
        },
        fail_on_query="query two",
    )
    orchestrator = _FakeOrchestrator()
    repo, session_factory = _in_memory_repository()

    monkeypatch.setattr(discovery_pipeline_module, "build_lead_source", lambda settings: source)
    monkeypatch.setattr(
        discovery_pipeline_module, "build_lead_orchestrator_agent", lambda settings: orchestrator
    )
    monkeypatch.setattr(discovery_pipeline_module, "build_lead_repository", lambda settings: repo)

    s = Settings(_env_file=None, discovery_max_results=5)
    leads = run_discovery_sweep(s, queries=["query one", "query two", "query three"])

    assert {lead.research.domain for lead in leads} == {"acme.com", "gamma.com"}
    with session_factory() as session:
        assert {r.domain for r in session.query(LeadRecord).all()} == {"acme.com", "gamma.com"}


def test_run_discovery_sweep_uses_settings_discovery_queries_by_default(monkeypatch):
    source = _FakeMultiQueryLeadSource(
        {"alpha": [Candidate(name="Acme", domain="acme.com")], "beta": [Candidate(name="Beta", domain="beta.com")]}
    )
    orchestrator = _FakeOrchestrator()
    repo, _ = _in_memory_repository()

    monkeypatch.setattr(discovery_pipeline_module, "build_lead_source", lambda settings: source)
    monkeypatch.setattr(
        discovery_pipeline_module, "build_lead_orchestrator_agent", lambda settings: orchestrator
    )
    monkeypatch.setattr(discovery_pipeline_module, "build_lead_repository", lambda settings: repo)

    s = Settings(_env_file=None, discovery_max_results=5, discovery_queries="alpha,beta")
    leads = run_discovery_sweep(s)

    assert orchestrator.targets_seen == ["acme.com", "beta.com"]
    assert len(leads) == 2


def test_run_discovery_sweep_with_no_queries_returns_empty_without_building_collaborators():
    s = Settings(_env_file=None, discovery_queries="")

    # No monkeypatching of build_lead_source/build_lead_orchestrator_agent/build_lead_repository:
    # an empty query list must short-circuit before any of them are ever called.
    leads = run_discovery_sweep(s)

    assert leads == []
