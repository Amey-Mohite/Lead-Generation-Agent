# Phase 8: API Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

> **Execution note:** The user commits/pushes to GitHub themselves. Do **not** run `git commit`
> or `git push`. End each task by reporting exactly what changed for the user to review and
> commit.

**Goal:** Expose the existing lead-generation pipeline (single-lead runs, discovery sweeps, and
persisted leads) over HTTP via FastAPI, so it can be triggered and queried by something other than
a CLI script — most immediately, Phase 9's dashboard.

**Architecture:** Two `POST` endpoints create a background job and return a `job_id` immediately
(202 Accepted); a `GET /v1/jobs/{job_id}` endpoint polls status/result. Two `GET` endpoints read
already-persisted leads straight from Postgres. All `/v1/*` routes sit behind a header-based API
key check that's a no-op when no key is configured. No pipeline logic is duplicated — the API
layer only calls the same `build_lead_orchestrator_agent`, `run_discovery_sweep`, and
`build_lead_repository` functions the CLI scripts already use.

**Tech Stack:** FastAPI (already a dependency), its built-in `BackgroundTasks` (no Celery/Redis),
`TestClient` for tests (executes background tasks synchronously within the test's request call).

## Global Constraints

- **Python:** 3.12+.
- **No network/real DB in tests:** every API test monkeypatches `build_lead_orchestrator_agent`,
  `run_discovery_sweep`, and `build_lead_repository` at the `app.api.leads` module level (same
  pattern the Phase 5-7 pipeline tests already use) — never the real configured provider/database.
- **`JobStore` is an in-memory `dict`**, not a Postgres table — job status is ephemeral process
  state; the durable output (the `Lead`(s) a job produces) is already saved to the `leads` table by
  the existing pipeline the moment it completes.
- **Auth is a no-op when `Settings.api_key` is unset** — matches this project's existing "no key
  configured → skip" pattern for LLM provider keys. `/health` and `/ready` stay unauthenticated
  (Kubernetes probes can't send custom headers).
- **A failed background job is caught and recorded**, not left to crash silently — `JobStore.mark_failed(job_id, str(exc))`, consistent with the per-candidate/per-query resilience already
  built into the pipeline in Phase 7.
- **Every task ends** with: tests green, then report the changes to the user for review/commit.

## File Structure

```
app/
  api/
    jobs.py        # Job model + JobStore (in-memory) + get_job_store()
    auth.py         # require_api_key dependency
    leads.py        # router: POST /v1/leads, POST /v1/discovery, GET /v1/jobs/{id},
                     #         GET /v1/leads, GET /v1/leads/{domain}
  db/
    repository.py   # + list_leads(), get_by_domain()
  main.py            # + registers the leads router
tests/
  api/
    __init__.py
    test_jobs.py
    test_auth.py
    test_leads.py
  db/
    test_repository.py   # + list_leads/get_by_domain tests
docs/
  learning/phase-8-api-layer.md
```

---

### Task 1: `Job` model + `JobStore`

**Files:**
- Create: `app/api/jobs.py`
- Test: `tests/api/__init__.py` (empty), `tests/api/test_jobs.py`

**Interfaces:**
- Consumes: nothing outside the standard library and `pydantic`.
- Produces:
  - `Job(BaseModel)`: `job_id: str`, `kind: Literal["lead", "discovery"]`,
    `status: Literal["queued", "running", "done", "failed"]`, `created_at: datetime`,
    `finished_at: datetime | None = None`, `result: Any = None`, `error: str | None = None`.
  - `JobStore`: `.create(kind: Literal["lead", "discovery"]) -> Job`, `.mark_running(job_id: str)
    -> None`, `.mark_done(job_id: str, result: Any) -> None`, `.mark_failed(job_id: str, error: str)
    -> None`, `.get(job_id: str) -> Job | None`.
  - `get_job_store() -> JobStore` — an `lru_cache`d module-level singleton.

- [ ] **Step 1: Write the failing test** — `tests/api/test_jobs.py`

```python
from app.api.jobs import JobStore


def test_create_returns_a_queued_job():
    store = JobStore()
    job = store.create(kind="lead")
    assert job.status == "queued"
    assert job.kind == "lead"
    assert job.result is None
    assert job.error is None


def test_mark_running_updates_status():
    store = JobStore()
    job = store.create(kind="lead")
    store.mark_running(job.job_id)
    assert store.get(job.job_id).status == "running"


def test_mark_done_sets_result_and_finished_at():
    store = JobStore()
    job = store.create(kind="discovery")
    store.mark_done(job.job_id, {"some": "result"})
    updated = store.get(job.job_id)
    assert updated.status == "done"
    assert updated.result == {"some": "result"}
    assert updated.finished_at is not None


def test_mark_failed_sets_error_and_finished_at():
    store = JobStore()
    job = store.create(kind="lead")
    store.mark_failed(job.job_id, "boom")
    updated = store.get(job.job_id)
    assert updated.status == "failed"
    assert updated.error == "boom"
    assert updated.finished_at is not None


def test_get_returns_none_for_unknown_job_id():
    store = JobStore()
    assert store.get("nonexistent") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/api/test_jobs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.jobs'`. Create the empty
`tests/api/__init__.py` first if collection errors on a missing package.

- [ ] **Step 3: Create `app/api/jobs.py`**

```python
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel


class Job(BaseModel):
    job_id: str
    kind: Literal["lead", "discovery"]
    status: Literal["queued", "running", "done", "failed"]
    created_at: datetime
    finished_at: datetime | None = None
    result: Any = None
    error: str | None = None


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self, kind: Literal["lead", "discovery"]) -> Job:
        job = Job(
            job_id=str(uuid.uuid4()),
            kind=kind,
            status="queued",
            created_at=datetime.now(timezone.utc),
        )
        self._jobs[job.job_id] = job
        return job

    def mark_running(self, job_id: str) -> None:
        self._jobs[job_id].status = "running"

    def mark_done(self, job_id: str, result: Any) -> None:
        job = self._jobs[job_id]
        job.status = "done"
        job.result = result
        job.finished_at = datetime.now(timezone.utc)

    def mark_failed(self, job_id: str, error: str) -> None:
        job = self._jobs[job_id]
        job.status = "failed"
        job.error = error
        job.finished_at = datetime.now(timezone.utc)

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)


@lru_cache
def get_job_store() -> JobStore:
    return JobStore()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/api/test_jobs.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Report changes** to the user for review/commit.

---

### Task 2: `require_api_key` dependency

**Files:**
- Create: `app/api/auth.py`
- Test: `tests/api/test_auth.py`

**Interfaces:**
- Consumes: `Settings`, `get_settings` (`app.config`).
- Produces: `require_api_key(x_api_key: str | None = Header(default=None), settings: Settings =
  Depends(get_settings)) -> None` — a FastAPI dependency; raises `HTTPException(status_code=401)`
  when `settings.api_key` is set and `x_api_key` doesn't match it; no-ops (including when
  `x_api_key` is `None`) when `settings.api_key` is unset.

- [ ] **Step 1: Write the failing test** — `tests/api/test_auth.py`

```python
import pytest
from fastapi import HTTPException

from app.api.auth import require_api_key
from app.config import Settings


def test_require_api_key_allows_when_no_key_configured():
    settings = Settings(_env_file=None, api_key=None)
    require_api_key(x_api_key=None, settings=settings)  # does not raise


def test_require_api_key_rejects_missing_header_when_key_configured():
    settings = Settings(_env_file=None, api_key="secret123")
    with pytest.raises(HTTPException) as exc_info:
        require_api_key(x_api_key=None, settings=settings)
    assert exc_info.value.status_code == 401


def test_require_api_key_rejects_wrong_header_when_key_configured():
    settings = Settings(_env_file=None, api_key="secret123")
    with pytest.raises(HTTPException) as exc_info:
        require_api_key(x_api_key="wrong", settings=settings)
    assert exc_info.value.status_code == 401


def test_require_api_key_accepts_correct_header():
    settings = Settings(_env_file=None, api_key="secret123")
    require_api_key(x_api_key="secret123", settings=settings)  # does not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/api/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.auth'`.

- [ ] **Step 3: Create `app/api/auth.py`**

```python
from fastapi import Depends, Header, HTTPException

from app.config import Settings, get_settings


def require_api_key(
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.api_key:
        return
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid or missing API key")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/api/test_auth.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Report changes** to the user for review/commit.

---

### Task 3: `LeadRepository.list_leads()` / `.get_by_domain()`

**Files:**
- Modify: `app/db/repository.py`
- Test: `tests/db/test_repository.py`

**Interfaces:**
- Consumes: `LeadRecord` (`app.db.models`), existing `LeadRepository` internals.
- Produces:
  - `LeadRepository.list_leads(status: str | None = None, limit: int = 50, offset: int = 0) ->
    list[LeadRecord]` — ordered by `last_seen_at` descending, optionally filtered by `status`.
  - `LeadRepository.get_by_domain(domain: str) -> LeadRecord | None`.

- [ ] **Step 1: Write the failing test** — append to `tests/db/test_repository.py`

```python
from datetime import timedelta


def _insert_record(session_factory, domain: str, company_name: str = "Acme",
                    status: str = "qualified", last_seen_at=None) -> None:
    now = last_seen_at or datetime.now(timezone.utc)
    with session_factory() as session:
        session.add(
            LeadRecord(
                domain=domain, company_name=company_name, status=status, score=80,
                reasoning="ok", summary="s", key_facts=[], contacts=[], sources=[],
                first_seen_at=now, last_seen_at=now,
            )
        )
        session.commit()


def test_list_leads_returns_all_by_default_most_recent_first():
    repo, session_factory = _repository()
    base = datetime.now(timezone.utc)
    _insert_record(session_factory, "acme.com", last_seen_at=base)
    _insert_record(session_factory, "beta.com", company_name="Beta", last_seen_at=base + timedelta(seconds=10))

    results = repo.list_leads()

    assert [r.domain for r in results] == ["beta.com", "acme.com"]


def test_list_leads_filters_by_status():
    repo, session_factory = _repository()
    base = datetime.now(timezone.utc)
    _insert_record(session_factory, "acme.com", status="disqualified", last_seen_at=base)
    _insert_record(session_factory, "beta.com", company_name="Beta", status="qualified", last_seen_at=base + timedelta(seconds=10))

    qualified = repo.list_leads(status="qualified")
    disqualified = repo.list_leads(status="disqualified")

    assert [r.domain for r in qualified] == ["beta.com"]
    assert [r.domain for r in disqualified] == ["acme.com"]


def test_list_leads_respects_limit_and_offset():
    repo, session_factory = _repository()
    base = datetime.now(timezone.utc)
    _insert_record(session_factory, "acme.com", last_seen_at=base)
    _insert_record(session_factory, "beta.com", company_name="Beta", last_seen_at=base + timedelta(seconds=10))
    _insert_record(session_factory, "gamma.com", company_name="Gamma", last_seen_at=base + timedelta(seconds=20))

    page = repo.list_leads(limit=1, offset=1)

    assert [r.domain for r in page] == ["beta.com"]


def test_get_by_domain_returns_the_matching_record():
    repo, session_factory = _repository()
    _insert_record(session_factory, "acme.com", company_name="Acme")

    record = repo.get_by_domain("acme.com")

    assert record is not None
    assert record.company_name == "Acme"


def test_get_by_domain_returns_none_when_not_found():
    repo, _ = _repository()
    assert repo.get_by_domain("nonexistent.com") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/db/test_repository.py -v`
Expected: FAIL — `AttributeError: 'LeadRepository' object has no attribute 'list_leads'`.

- [ ] **Step 3: Add the two methods to `app/db/repository.py`** — insert after `all_domains`:

```python
    def list_leads(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[LeadRecord]:
        with self._session_factory() as session:
            stmt = select(LeadRecord).order_by(LeadRecord.last_seen_at.desc())
            if status is not None:
                stmt = stmt.where(LeadRecord.status == status)
            stmt = stmt.offset(offset).limit(limit)
            return list(session.scalars(stmt))

    def get_by_domain(self, domain: str) -> LeadRecord | None:
        with self._session_factory() as session:
            return session.scalar(select(LeadRecord).where(LeadRecord.domain == domain))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/db/test_repository.py -v`
Expected: PASS (11 passed — 6 existing + 5 new).

- [ ] **Step 5: Report changes** to the user for review/commit.

---

### Task 4: `app/api/leads.py` router — `POST /v1/leads` + `GET /v1/jobs/{job_id}`

**Files:**
- Create: `app/api/leads.py`
- Modify: `app/main.py`
- Test: `tests/api/test_leads.py`

**Interfaces:**
- Consumes: `Job`, `JobStore`, `get_job_store` (Task 1); `require_api_key` (Task 2);
  `build_lead_orchestrator_agent` (`app.agents.orchestrator_agent`); `build_lead_repository`
  (`app.db.repository`); `Settings`, `get_settings` (`app.config`).
- Produces:
  - `router = APIRouter(prefix="/v1", tags=["leads"], dependencies=[Depends(require_api_key)])`.
  - `LeadRunRequest(BaseModel)`: `target: str`.
  - `JobAccepted(BaseModel)`: `job_id: str`, `status: str`.
  - `POST /v1/leads` → 202, body `LeadRunRequest`, returns `JobAccepted`.
  - `GET /v1/jobs/{job_id}` → `Job` (200) or 404 if unknown.

- [ ] **Step 1: Write the failing test** — `tests/api/test_leads.py`

```python
from fastapi.testclient import TestClient

import app.api.leads as leads_module
from app.api.jobs import JobStore, get_job_store
from app.config import Settings, get_settings
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/api/test_leads.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.leads'`.

- [ ] **Step 3: Create `app/api/leads.py`**

```python
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from app.agents.orchestrator_agent import build_lead_orchestrator_agent
from app.api.auth import require_api_key
from app.api.jobs import Job, JobStore, get_job_store
from app.config import Settings, get_settings
from app.db.repository import build_lead_repository

router = APIRouter(prefix="/v1", tags=["leads"], dependencies=[Depends(require_api_key)])


class LeadRunRequest(BaseModel):
    target: str


class JobAccepted(BaseModel):
    job_id: str
    status: str


def _run_lead_job(job_store: JobStore, job_id: str, settings: Settings, target: str) -> None:
    job_store.mark_running(job_id)
    try:
        orchestrator = build_lead_orchestrator_agent(settings)
        lead = orchestrator.run(target)
        build_lead_repository(settings).save(lead)
        job_store.mark_done(job_id, lead)
    except Exception as exc:
        job_store.mark_failed(job_id, str(exc))


@router.post("/leads", status_code=202, response_model=JobAccepted)
def trigger_lead_run(
    body: LeadRunRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    job_store: JobStore = Depends(get_job_store),
) -> JobAccepted:
    job = job_store.create(kind="lead")
    background_tasks.add_task(_run_lead_job, job_store, job.job_id, settings, body.target)
    return JobAccepted(job_id=job.job_id, status=job.status)


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str, job_store: JobStore = Depends(get_job_store)) -> Job:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job
```

- [ ] **Step 4: Register the router in `app/main.py`**

```python
from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.leads import router as leads_router
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.include_router(health_router)
    app.include_router(leads_router)
    return app


app = create_app()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/api/test_leads.py -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all prior tests + this task's new tests green.

- [ ] **Step 7: Report changes** to the user for review/commit.

---

### Task 5: `POST /v1/discovery`

**Files:**
- Modify: `app/api/leads.py`
- Test: `tests/api/test_leads.py`

**Interfaces:**
- Consumes: `parse_discovery_queries`, `run_discovery_sweep` (`app.agents.discovery_pipeline`);
  `Job`, `JobStore`, `get_job_store`, `router`, `JobAccepted` (Task 4).
- Produces: `DiscoveryRunRequest(BaseModel)`: `query: str | None = None`, `queries: list[str] |
  None = None`, `max_results: int | None = None`. `POST /v1/discovery` → 202, returns
  `JobAccepted`. Query resolution order: `queries` if given, else `[query]` if given, else
  `parse_discovery_queries(settings.discovery_queries)` if non-empty, else
  `["credit unions in the UK"]`.

- [ ] **Step 1: Write the failing test** — append to `tests/api/test_leads.py`

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/api/test_leads.py -k discovery -v`
Expected: FAIL — `404` (no `/v1/discovery` route registered yet).

- [ ] **Step 3: Add to `app/api/leads.py`** — new import line and new code at the end of the file:

```python
from app.agents.discovery_pipeline import parse_discovery_queries, run_discovery_sweep
```

(Add this to the existing import block at the top, alongside the `orchestrator_agent` import.)

```python
class DiscoveryRunRequest(BaseModel):
    query: str | None = None
    queries: list[str] | None = None
    max_results: int | None = None


def _resolve_discovery_queries(body: "DiscoveryRunRequest", settings: Settings) -> list[str]:
    if body.queries:
        return body.queries
    if body.query:
        return [body.query]
    configured = parse_discovery_queries(settings.discovery_queries)
    if configured:
        return configured
    return ["credit unions in the UK"]


def _run_discovery_job(
    job_store: JobStore, job_id: str, settings: Settings, queries: list[str],
    max_results: int | None,
) -> None:
    job_store.mark_running(job_id)
    try:
        leads = run_discovery_sweep(settings, queries=queries, max_results=max_results)
        job_store.mark_done(job_id, leads)
    except Exception as exc:
        job_store.mark_failed(job_id, str(exc))


@router.post("/discovery", status_code=202, response_model=JobAccepted)
def trigger_discovery_run(
    body: DiscoveryRunRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    job_store: JobStore = Depends(get_job_store),
) -> JobAccepted:
    queries = _resolve_discovery_queries(body, settings)
    job = job_store.create(kind="discovery")
    background_tasks.add_task(
        _run_discovery_job, job_store, job.job_id, settings, queries, body.max_results
    )
    return JobAccepted(job_id=job.job_id, status=job.status)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/api/test_leads.py -k discovery -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all prior tests + this task's new tests green.

- [ ] **Step 6: Report changes** to the user for review/commit.

---

### Task 6: `GET /v1/leads` + `GET /v1/leads/{domain}`

**Files:**
- Modify: `app/api/leads.py`
- Test: `tests/api/test_leads.py`

**Interfaces:**
- Consumes: `LeadRecord` (`app.db.models`); `LeadRepository.list_leads`/`.get_by_domain` (Task 3).
- Produces: `LeadRecordOut(BaseModel)` (`from_attributes=True`) mirroring every `LeadRecord`
  column. `GET /v1/leads` → 200, `list[LeadRecordOut]`, query params `status`, `limit` (default
  50), `offset` (default 0). `GET /v1/leads/{domain}` → 200 `LeadRecordOut` or 404.

- [ ] **Step 1: Write the failing test** — append to `tests/api/test_leads.py`

```python
from datetime import datetime, timezone

from app.db.models import LeadRecord


def _fake_record(domain: str, company_name: str) -> LeadRecord:
    now = datetime.now(timezone.utc)
    return LeadRecord(
        id=1, domain=domain, company_name=company_name, industry="Financial Services",
        status="qualified", score=85, reasoning="Good fit.", summary="A company.",
        key_facts=["fact1"], contacts=[], sources=["https://example.com"],
        outreach_subject="Hi", outreach_body="Hello", first_seen_at=now, last_seen_at=now,
    )


def test_list_leads_returns_persisted_records(monkeypatch):
    class _FakeReadRepo:
        def list_leads(self, status=None, limit=50, offset=0):
            return [_fake_record("acme.com", "Acme")]

    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: _FakeReadRepo())

    client = _client_with_overrides(Settings(_env_file=None), JobStore())
    resp = client.get("/v1/leads")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["domain"] == "acme.com"


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/api/test_leads.py -k "list_leads or get_lead" -v`
Expected: FAIL — `404` (no `/v1/leads` GET routes registered yet).

- [ ] **Step 3: Add to `app/api/leads.py`** — update the `pydantic` import line and add new code:

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
```

(Replace the existing `from pydantic import BaseModel` line with the above.)

```python
class LeadRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    domain: str
    company_name: str
    industry: str | None
    status: str
    score: int
    reasoning: str
    summary: str
    key_facts: list[str]
    contacts: list[dict]
    sources: list[str]
    outreach_subject: str | None
    outreach_body: str | None
    first_seen_at: datetime
    last_seen_at: datetime


@router.get("/leads", response_model=list[LeadRecordOut])
def list_leads(
    status: Literal["qualified", "disqualified"] | None = None,
    limit: int = 50,
    offset: int = 0,
    settings: Settings = Depends(get_settings),
) -> list[LeadRecordOut]:
    repository = build_lead_repository(settings)
    records = repository.list_leads(status=status, limit=limit, offset=offset)
    return [LeadRecordOut.model_validate(r) for r in records]


@router.get("/leads/{domain}", response_model=LeadRecordOut)
def get_lead(domain: str, settings: Settings = Depends(get_settings)) -> LeadRecordOut:
    repository = build_lead_repository(settings)
    record = repository.get_by_domain(domain)
    if record is None:
        raise HTTPException(status_code=404, detail="lead not found")
    return LeadRecordOut.model_validate(record)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/api/test_leads.py -v`
Expected: PASS (13 passed — all of Tasks 4, 5, and 6's tests in this file).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all green (Phase 1-8), no network/DB required.

- [ ] **Step 6: Manual smoke test**

Run: `./.venv/Scripts/python.exe -m uvicorn app.main:app --reload`
Then in a second terminal: `curl http://localhost:8000/docs` should return the Swagger UI HTML,
and it should list `/v1/leads` (GET+POST), `/v1/discovery`, `/v1/jobs/{job_id}`,
`/v1/leads/{domain}` alongside the existing `/health`/`/ready`.

- [ ] **Step 7: Report changes** to the user for review/commit.

---

### Task 7: Learning guide + index updates

**Files:**
- Create: `docs/learning/phase-8-api-layer.md`
- Modify: `docs/learning/README.md`
- Modify: `README.md` (Status section)

**Interfaces:** none (documentation only).

- [ ] **Step 1: Write `docs/learning/phase-8-api-layer.md`** — same structure as the Phase 1-7
  guides. Must cover:
  - **What & why** — why background jobs + polling instead of synchronous responses (a discovery
    sweep can take many minutes, confirmed during Phase 7 testing); why an in-memory `JobStore`
    instead of a Postgres table (job status is ephemeral, the durable output is already in the
    `leads` table the moment a job completes); why auth is a no-op when unset (matches the
    project's existing pattern for LLM provider keys).
  - **The flow** — `POST /v1/leads or /v1/discovery -> JobStore.create() -> 202 {job_id} ->
    BackgroundTasks runs the same pipeline the CLI scripts use -> JobStore.mark_done/mark_failed ->
    GET /v1/jobs/{job_id} to poll`.
  - **File-by-file walkthrough** — `app/api/jobs.py` (the `Job`/`JobStore` design, why it's a
    singleton via `lru_cache`); `app/api/auth.py` (the no-op-when-unset dependency, why it's
    applied at the router level not per-route); `app/api/leads.py` (why the API layer calls the
    same `build_lead_orchestrator_agent`/`run_discovery_sweep`/`build_lead_repository` the CLI
    scripts use rather than reimplementing anything; the discovery query resolution order).
  - **Key concepts table** — background-job-plus-polling for slow operations, ephemeral in-memory
    state vs. durable persisted state, no-op auth for frictionless local dev, testing
    `BackgroundTasks` synchronously via `TestClient`, `app.dependency_overrides` for per-test
    isolation.
  - **How to run & test** — `pytest tests/api tests/db -v`, explaining what each test file proves;
    `uvicorn app.main:app --reload` + `/docs` for manual exploration; a real `curl` example for
    triggering a lead run and polling its job.
  - **What's next** — Phase 9: Dashboard (React + Vite) — a minimal UI that calls this API to
    trigger runs, poll job status, and display persisted leads.

- [ ] **Step 2: Update `docs/learning/README.md`** — add a row to the phase-guides table:

```markdown
| [Phase 8 — API Layer](phase-8-api-layer.md) | The pipeline becomes a real HTTP service: background-job-plus-polling for slow discovery runs, an in-memory job store, no-op-when-unset API key auth, and read endpoints over the persisted `leads` table. |
```

And update the mental-model diagram's Phase 8 line:

```
Phase 8  API layer ........... FastAPI endpoints exposing the pipeline as a background-job service
```

- [ ] **Step 3: Update `README.md`** — change the Phase 8 status line and the "Current" marker:

```markdown
- [x] Phase 8 — API layer (background-job-plus-polling FastAPI endpoints; no-op-when-unset API key auth)
```

- [ ] **Step 4: Report changes** to the user for review/commit.

---

## Phase 8 Definition of Done

- `./.venv/Scripts/python.exe -m pytest -q` → all green (Phase 1-8), no network or real Postgres
  required.
- `POST /v1/leads` / `POST /v1/discovery` return 202 + `job_id` immediately; `GET
  /v1/jobs/{job_id}` reflects `queued` → `running` → `done`/`failed` with the actual result/error.
- `GET /v1/leads` / `GET /v1/leads/{domain}` read straight from the Phase 7 `leads` table.
- Every `/v1/*` route is open when `API_KEY` is unset and requires a matching `X-API-Key` header
  when it's set; `/health`/`/ready` remain unauthenticated.
- Manual smoke test: `/docs` shows all new routes; a real `curl` round-trip against a running
  server (with real keys) produces a genuine `Lead`.
- Learning guide written; README + learning index updated.

**Next phase (planned just-in-time after this one):** Phase 9 — Dashboard (React + Vite): a
minimal UI that calls this API to trigger runs, poll job status, and display persisted leads.
