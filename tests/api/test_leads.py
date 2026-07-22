from datetime import datetime, timezone

from fastapi.testclient import TestClient

import app.api.leads as leads_module
from app.api.jobs import JobStore, get_job_store
from app.config import Settings, get_settings
from app.db.models import LeadRecord
from app.main import create_app
from app.schemas.lead import Lead, Qualification
from app.schemas.research import ResearchBrief


class _FakeOrchestrator:
    def __init__(self, lead: Lead) -> None:
        self._lead = lead
        self.targets_seen: list[str] = []

    def run(self, target: str) -> Lead:
        self.targets_seen.append(target)
        return self._lead


class _FakeRepository:
    def __init__(self) -> None:
        self.saved: list[Lead] = []

    def save(self, lead: Lead) -> None:
        self.saved.append(lead)


def _lead_for(domain: str) -> Lead:
    return Lead(
        research=ResearchBrief(company_name="Acme", domain=domain, summary="A company."),
        qualification=Qualification(score=85, reasoning="Good fit."),
        status="qualified",
    )


def _client_with_overrides(settings: Settings, job_store: JobStore) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_job_store] = lambda: job_store
    return TestClient(app)


def test_trigger_lead_run_returns_202_and_queued_job(monkeypatch):
    lead = _lead_for("acme.com")
    monkeypatch.setattr(leads_module, "build_lead_orchestrator_agent", lambda settings: _FakeOrchestrator(lead))
    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: _FakeRepository())

    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    resp = client.post("/v1/leads", json={"target": "acme.com"})

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert "job_id" in body


def test_job_is_done_after_background_task_runs(monkeypatch):
    lead = _lead_for("acme.com")
    fake_orchestrator = _FakeOrchestrator(lead)
    fake_repo = _FakeRepository()
    monkeypatch.setattr(leads_module, "build_lead_orchestrator_agent", lambda settings: fake_orchestrator)
    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: fake_repo)

    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    job_id = client.post("/v1/leads", json={"target": "acme.com"}).json()["job_id"]

    get_resp = client.get(f"/v1/jobs/{job_id}")

    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["status"] == "done"
    assert body["result"]["research"]["domain"] == "acme.com"
    assert fake_orchestrator.targets_seen == ["acme.com"]
    assert len(fake_repo.saved) == 1


def test_job_is_failed_when_orchestrator_raises(monkeypatch):
    class _FailingOrchestrator:
        def run(self, target: str) -> Lead:
            raise RuntimeError("boom")

    monkeypatch.setattr(leads_module, "build_lead_orchestrator_agent", lambda settings: _FailingOrchestrator())
    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: _FakeRepository())

    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    job_id = client.post("/v1/leads", json={"target": "acme.com"}).json()["job_id"]

    get_resp = client.get(f"/v1/jobs/{job_id}")

    assert get_resp.json()["status"] == "failed"
    assert "boom" in get_resp.json()["error"]


def test_get_job_returns_404_for_unknown_job_id():
    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    resp = client.get("/v1/jobs/nonexistent")
    assert resp.status_code == 404


def test_requires_api_key_when_configured():
    client = _client_with_overrides(Settings(_env_file=None, api_key="secret123"), JobStore())
    resp = client.post("/v1/leads", json={"target": "acme.com"})
    assert resp.status_code == 401


def test_accepts_correct_api_key(monkeypatch):
    lead = _lead_for("acme.com")
    monkeypatch.setattr(leads_module, "build_lead_orchestrator_agent", lambda settings: _FakeOrchestrator(lead))
    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: _FakeRepository())

    client = _client_with_overrides(Settings(_env_file=None, api_key="secret123"), JobStore())
    resp = client.post("/v1/leads", json={"target": "acme.com"}, headers={"X-API-Key": "secret123"})

    assert resp.status_code == 202


def test_trigger_discovery_run_with_explicit_query(monkeypatch):
    lead = _lead_for("acme.com")

    def _fake_sweep(settings, queries=None, max_results=None):
        assert queries == ["credit unions in the UK"]
        return [lead]

    monkeypatch.setattr(leads_module, "run_discovery_sweep", _fake_sweep)

    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    job_id = client.post("/v1/discovery", json={"query": "credit unions in the UK"}).json()["job_id"]

    get_resp = client.get(f"/v1/jobs/{job_id}")

    assert get_resp.json()["status"] == "done"
    assert len(get_resp.json()["result"]) == 1


def test_trigger_discovery_run_with_explicit_queries_list(monkeypatch):
    def _fake_sweep(settings, queries=None, max_results=None):
        assert queries == ["a", "b"]
        return []

    monkeypatch.setattr(leads_module, "run_discovery_sweep", _fake_sweep)

    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    job_id = client.post("/v1/discovery", json={"queries": ["a", "b"]}).json()["job_id"]

    get_resp = client.get(f"/v1/jobs/{job_id}")

    assert get_resp.json()["status"] == "done"


def test_trigger_discovery_run_falls_back_to_settings_discovery_queries(monkeypatch):
    def _fake_sweep(settings, queries=None, max_results=None):
        assert queries == ["credit unions", "building societies"]
        return []

    monkeypatch.setattr(leads_module, "run_discovery_sweep", _fake_sweep)

    settings = Settings(_env_file=None, discovery_queries="credit unions,building societies")
    client = _client_with_overrides(settings, JobStore())
    job_id = client.post("/v1/discovery", json={}).json()["job_id"]

    get_resp = client.get(f"/v1/jobs/{job_id}")

    assert get_resp.json()["status"] == "done"


def test_trigger_discovery_run_falls_back_to_default_query_when_nothing_configured(monkeypatch):
    def _fake_sweep(settings, queries=None, max_results=None):
        assert queries == ["credit unions in the UK"]
        return []

    monkeypatch.setattr(leads_module, "run_discovery_sweep", _fake_sweep)

    settings = Settings(_env_file=None, discovery_queries="")
    client = _client_with_overrides(settings, JobStore())
    job_id = client.post("/v1/discovery", json={}).json()["job_id"]

    get_resp = client.get(f"/v1/jobs/{job_id}")

    assert get_resp.json()["status"] == "done"


def _fake_record(domain: str, company_name: str, approval_status: str | None = "pending") -> LeadRecord:
    now = datetime.now(timezone.utc)
    return LeadRecord(
        id=1, domain=domain, company_name=company_name, industry="Financial Services",
        status="qualified", score=85, reasoning="Good fit.", summary="A company.",
        key_facts=["fact1"], contacts=[], sources=["https://example.com"],
        outreach_subject="Hi", outreach_body="Hello", approval_status=approval_status,
        first_seen_at=now, last_seen_at=now,
    )


def test_list_leads_returns_persisted_records(monkeypatch):
    class _FakeReadRepo:
        def list_leads(self, status=None, approval_status=None, limit=50, offset=0):
            return [_fake_record("acme.com", "Acme")]

    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: _FakeReadRepo())

    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    resp = client.get("/v1/leads")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["domain"] == "acme.com"


def test_list_leads_filters_by_approval_status(monkeypatch):
    captured = {}

    class _FakeReadRepo:
        def list_leads(self, status=None, approval_status=None, limit=50, offset=0):
            captured["approval_status"] = approval_status
            return [_fake_record("acme.com", "Acme")]

    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: _FakeReadRepo())

    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    resp = client.get("/v1/leads?approval_status=pending")

    assert resp.status_code == 200
    assert captured["approval_status"] == "pending"
    assert resp.json()[0]["approval_status"] == "pending"


def test_get_lead_returns_the_matching_record(monkeypatch):
    class _FakeReadRepo:
        def get_by_domain(self, domain):
            return _fake_record(domain, "Acme") if domain == "acme.com" else None

    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: _FakeReadRepo())

    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    resp = client.get("/v1/leads/acme.com")

    assert resp.status_code == 200
    assert resp.json()["company_name"] == "Acme"


def test_get_lead_returns_404_when_not_found(monkeypatch):
    class _FakeReadRepo:
        def get_by_domain(self, domain):
            return None

    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: _FakeReadRepo())

    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    resp = client.get("/v1/leads/nonexistent.com")

    assert resp.status_code == 404


class _FakeApprovalRepo:
    def __init__(self, record):
        self._record = record
        self.calls: list[tuple[str, str]] = []

    def get_by_domain(self, domain):
        return self._record if domain == self._record.domain else None

    def set_approval_status(self, domain, approval_status):
        self.calls.append((domain, approval_status))
        self._record.approval_status = approval_status
        return self._record


def test_decide_lead_approval_approves_a_pending_lead(monkeypatch):
    record = _fake_record("acme.com", "Acme", approval_status="pending")
    repo = _FakeApprovalRepo(record)
    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: repo)

    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    resp = client.post("/v1/leads/acme.com/approval", json={"decision": "approved"})

    assert resp.status_code == 200
    assert resp.json()["approval_status"] == "approved"
    assert repo.calls == [("acme.com", "approved")]


def test_decide_lead_approval_rejects_a_pending_lead(monkeypatch):
    record = _fake_record("acme.com", "Acme", approval_status="pending")
    repo = _FakeApprovalRepo(record)
    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: repo)

    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    resp = client.post("/v1/leads/acme.com/approval", json={"decision": "rejected"})

    assert resp.status_code == 200
    assert resp.json()["approval_status"] == "rejected"


def test_decide_lead_approval_404_for_unknown_domain(monkeypatch):
    record = _fake_record("acme.com", "Acme", approval_status="pending")
    repo = _FakeApprovalRepo(record)
    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: repo)

    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    resp = client.post("/v1/leads/nonexistent.com/approval", json={"decision": "approved"})

    assert resp.status_code == 404


def test_decide_lead_approval_400_when_not_pending(monkeypatch):
    record = _fake_record("acme.com", "Acme", approval_status="sent")
    repo = _FakeApprovalRepo(record)
    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: repo)

    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    resp = client.post("/v1/leads/acme.com/approval", json={"decision": "approved"})

    assert resp.status_code == 400


def test_mark_lead_sent_marks_an_approved_lead_as_sent(monkeypatch):
    record = _fake_record("acme.com", "Acme", approval_status="approved")
    repo = _FakeApprovalRepo(record)
    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: repo)

    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    resp = client.post("/v1/leads/acme.com/sent")

    assert resp.status_code == 200
    assert resp.json()["approval_status"] == "sent"
    assert repo.calls == [("acme.com", "sent")]


def test_mark_lead_sent_404_for_unknown_domain(monkeypatch):
    record = _fake_record("acme.com", "Acme", approval_status="approved")
    repo = _FakeApprovalRepo(record)
    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: repo)

    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    resp = client.post("/v1/leads/nonexistent.com/sent")

    assert resp.status_code == 404


def test_mark_lead_sent_400_when_not_approved(monkeypatch):
    record = _fake_record("acme.com", "Acme", approval_status="pending")
    repo = _FakeApprovalRepo(record)
    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: repo)

    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    resp = client.post("/v1/leads/acme.com/sent")

    assert resp.status_code == 400
