import app.agents.discovery_pipeline as discovery_pipeline_module
from app.agents.discovery_pipeline import discover_and_qualify_leads, run_discovery_pipeline
from app.config import Settings
from app.schemas.discovery import Candidate
from app.schemas.lead import Lead, Qualification
from app.schemas.research import ResearchBrief


def _lead_for(target: str) -> Lead:
    return Lead(
        research=ResearchBrief(company_name=target, summary=f"Summary for {target}"),
        qualification=Qualification(score=80, reasoning="ok"),
        status="qualified",
    )


class _FakeLeadSource:
    def __init__(self, candidates: list[Candidate]):
        self._candidates = candidates

    def discover(self, query: str, max_results: int) -> list[Candidate]:
        return self._candidates[:max_results]


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.targets_seen: list[str] = []

    def run(self, target: str) -> Lead:
        self.targets_seen.append(target)
        return _lead_for(target)


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
    monkeypatch.setattr(discovery_pipeline_module, "build_lead_source", lambda settings: fake_source)
    monkeypatch.setattr(
        discovery_pipeline_module, "build_lead_orchestrator_agent", lambda settings: fake_orchestrator
    )

    s = Settings(_env_file=None, discovery_max_results=3)
    leads = run_discovery_pipeline(s, "credit unions")

    assert len(leads) == 3


def test_run_discovery_pipeline_explicit_max_results_overrides_settings(monkeypatch):
    fake_source = _FakeLeadSource([Candidate(name="Acme", domain="acme.com")] * 5)
    fake_orchestrator = _FakeOrchestrator()
    monkeypatch.setattr(discovery_pipeline_module, "build_lead_source", lambda settings: fake_source)
    monkeypatch.setattr(
        discovery_pipeline_module, "build_lead_orchestrator_agent", lambda settings: fake_orchestrator
    )

    s = Settings(_env_file=None, discovery_max_results=3)
    leads = run_discovery_pipeline(s, "credit unions", max_results=1)

    assert len(leads) == 1
