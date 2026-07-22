# Phase 10: n8n Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let n8n orchestrate the existing hand-built Python lead-gen agent through three workflows
(trigger/ingestion, human-approval + send, alerting), without n8n re-implementing any agent logic.

**Architecture:** The app grows a small approval-state machine on top of the existing `leads` table
plus two new endpoints n8n calls to move a lead through it, and a push-based alert hook the app
calls into n8n on job failure. Three hand-authored n8n workflow JSON files (real, importable
definitions) live in a new `n8n/` directory. `deploy/docker-compose.yml` gains an `n8n` service.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, `httpx` (already a dependency), n8n (Docker image,
`n8nio/n8n`), no new Python packages.

## Global Constraints

- **No git commits by anyone but the user, ever, at any point** — same as Phases 8-9. All changes
  stay in the working tree. Per-task review diffs are generated via file snapshots +
  `git diff --no-index`, never real commit ranges.
- **No new Python dependencies** — `httpx` (used for the alert webhook POST) is already a project
  dependency since Phase 3. Nothing new needs adding to `pyproject.toml`.
- **The n8n workflow JSON files are validated only for well-formed structure** (valid JSON, expected
  node types present) — per explicit user direction, they are **not** imported into a running n8n
  instance, executed, or live-tested as part of this phase. No task in this plan should attempt to
  start n8n, call a real Slack/Gmail API, or require real credentials.
- **Every new Python test must pass without any real n8n/Slack/Gmail network call or credential** —
  same "no-op when unset" / "mock what would otherwise be a live network call" discipline this
  project has followed since Phase 2's LLM provider keys and Phase 9's Langfuse config.
- **Backward compatibility**: every new/changed function parameter must default such that all
  pre-existing callers and tests are completely unaffected (matches `JobStore.mark_failed`'s new
  optional `settings` parameter in Task 6).
- Full test suite starts this phase at **171 passing** (Phase 9 + its final-review fix).

---

### Task 1: `approval_status` column on `LeadRecord` + migration

**Files:**
- Modify: `app/db/models.py`
- Create: `alembic/versions/b4f9c2a1d8e3_add_approval_status_to_leads.py`
- Test: `tests/db/test_models.py` (new file)

**Interfaces:**
- Produces: `LeadRecord.approval_status: str | None` (nullable, no default) — consumed by every
  later task in this plan (`LeadRepository`, the API endpoints, and the "pending" state machine).

- [ ] **Step 1: Write the failing test**

Create `tests/db/test_models.py`:
```python
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, LeadRecord


def test_lead_record_approval_status_column_defaults_to_none():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc)

    with Session() as session:
        record = LeadRecord(
            domain="acme.com", company_name="Acme", status="qualified", score=80,
            reasoning="ok", summary="s", key_facts=[], contacts=[], sources=[],
            first_seen_at=now, last_seen_at=now,
        )
        session.add(record)
        session.commit()

        fetched = session.query(LeadRecord).filter_by(domain="acme.com").one()
        assert fetched.approval_status is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/db/test_models.py -v`
Expected: FAIL — `TypeError` or `AttributeError`, since `LeadRecord` has no `approval_status`
attribute yet (the test itself doesn't reference the attribute name directly at construction time,
so the failure will show up at the final `assert fetched.approval_status is None` line as an
`AttributeError`).

- [ ] **Step 3: Add the column to `LeadRecord`**

In `app/db/models.py`, add one line after the existing `outreach_body` column:
```python
    outreach_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```
(`String` is already imported at the top of this file from Phase 7.)

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/db/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Write the Alembic migration**

Create `alembic/versions/b4f9c2a1d8e3_add_approval_status_to_leads.py`:
```python
"""add approval_status to leads

Revision ID: b4f9c2a1d8e3
Revises: ae131006cca1
Create Date: 2026-07-22 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4f9c2a1d8e3'
down_revision: Union[str, Sequence[str], None] = 'ae131006cca1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('leads', sa.Column('approval_status', sa.String(length=20), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('leads', 'approval_status')
```

This migration is not executed by the test suite (this project's repository tests build schema via
`Base.metadata.create_all()` against an in-memory SQLite engine, bypassing Alembic entirely — the
same pattern the existing `ae131006cca1_create_leads_table.py` migration already isn't unit-tested
under). It's meant to be run by the user against their real Postgres database whenever they choose
(`alembic upgrade head`), which is outside this phase's "no live testing" scope.

- [ ] **Step 6: Run the full test suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `172 passed` (171 + 1 new).

---

### Task 2: `LeadRepository` — pending-status logic, filter, and transition method

**Files:**
- Modify: `app/db/repository.py`
- Test: `tests/db/test_repository.py`

**Interfaces:**
- Consumes: `LeadRecord.approval_status` (Task 1).
- Produces: `LeadRepository.list_leads(status=None, approval_status=None, limit=50, offset=0)`
  (new `approval_status` parameter); `LeadRepository.set_approval_status(domain: str,
  approval_status: str) -> LeadRecord | None` (new method) — both consumed by Task 3/4's API
  endpoints.

- [ ] **Step 1: Write the failing tests**

Add to `tests/db/test_repository.py` (after the existing `test_list_leads_respects_limit_and_offset`
test):
```python
def test_save_sets_pending_approval_status_for_new_qualified_lead():
    repo, session_factory = _repository()
    repo.save(_lead("acme.com"))

    with session_factory() as session:
        record = session.query(LeadRecord).filter_by(domain="acme.com").one()
        assert record.approval_status == "pending"


def test_save_never_sets_approval_status_for_disqualified_lead():
    repo, session_factory = _repository()
    lead = Lead(
        research=ResearchBrief(company_name="Acme", domain="acme.com", summary="A company."),
        qualification=Qualification(score=10, reasoning="not a fit"),
        status="disqualified",
    )
    repo.save(lead)

    with session_factory() as session:
        record = session.query(LeadRecord).filter_by(domain="acme.com").one()
        assert record.approval_status is None


def test_save_does_not_overwrite_existing_approval_status_on_resave():
    repo, session_factory = _repository()
    repo.save(_lead("acme.com"))
    repo.set_approval_status("acme.com", "approved")

    repo.save(_lead("acme.com", company_name="Acme Renamed"))  # re-saved, e.g. re-researched

    with session_factory() as session:
        record = session.query(LeadRecord).filter_by(domain="acme.com").one()
        assert record.company_name == "Acme Renamed"
        assert record.approval_status == "approved"  # untouched by the re-save


def test_list_leads_filters_by_approval_status():
    repo, session_factory = _repository()
    base = datetime.now(timezone.utc)
    _insert_record(session_factory, "acme.com", last_seen_at=base)
    _insert_record(session_factory, "beta.com", company_name="Beta", last_seen_at=base + timedelta(seconds=10))
    with session_factory() as session:
        session.query(LeadRecord).filter_by(domain="acme.com").one().approval_status = "pending"
        session.query(LeadRecord).filter_by(domain="beta.com").one().approval_status = "approved"
        session.commit()

    pending = repo.list_leads(approval_status="pending")
    approved = repo.list_leads(approval_status="approved")

    assert [r.domain for r in pending] == ["acme.com"]
    assert [r.domain for r in approved] == ["beta.com"]


def test_set_approval_status_updates_and_returns_the_record():
    repo, session_factory = _repository()
    repo.save(_lead("acme.com"))

    updated = repo.set_approval_status("acme.com", "approved")

    assert updated is not None
    assert updated.approval_status == "approved"


def test_set_approval_status_returns_none_for_unknown_domain():
    repo, _ = _repository()
    assert repo.set_approval_status("nonexistent.com", "approved") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/db/test_repository.py -v`
Expected: FAIL — `test_save_sets_pending_approval_status_for_new_qualified_lead` and
`test_save_never_sets_approval_status_for_disqualified_lead` fail on the `approval_status`
assertion (currently always `None`); the rest fail with `AttributeError: 'LeadRepository' object
has no attribute 'set_approval_status'` or `TypeError: list_leads() got an unexpected keyword
argument 'approval_status'`.

- [ ] **Step 3: Implement in `app/db/repository.py`**

Modify `save()` — add the pending-status logic right before `session.commit()`:
```python
    def save(self, lead: Lead) -> None:
        domain = lead.research.domain
        if not domain:
            return

        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            record = session.scalar(select(LeadRecord).where(LeadRecord.domain == domain))
            if record is None:
                record = LeadRecord(domain=domain, first_seen_at=now)
                session.add(record)

            record.company_name = lead.research.company_name
            record.industry = lead.research.industry
            record.status = lead.status
            record.score = lead.qualification.score
            record.reasoning = lead.qualification.reasoning
            record.summary = lead.research.summary
            record.key_facts = lead.research.key_facts
            record.contacts = [c.model_dump() for c in lead.research.contacts]
            record.sources = lead.research.sources
            record.outreach_subject = lead.outreach.subject if lead.outreach else None
            record.outreach_body = lead.outreach.body if lead.outreach else None
            if lead.status == "qualified" and record.approval_status is None:
                record.approval_status = "pending"
            record.last_seen_at = now
            session.commit()
```

Modify `list_leads()`:
```python
    def list_leads(
        self, status: str | None = None, approval_status: str | None = None,
        limit: int = 50, offset: int = 0
    ) -> list[LeadRecord]:
        with self._session_factory() as session:
            stmt = select(LeadRecord).order_by(LeadRecord.last_seen_at.desc())
            if status is not None:
                stmt = stmt.where(LeadRecord.status == status)
            if approval_status is not None:
                stmt = stmt.where(LeadRecord.approval_status == approval_status)
            stmt = stmt.offset(offset).limit(limit)
            return list(session.scalars(stmt))
```

Add a new method, right after `get_by_domain`:
```python
    def set_approval_status(self, domain: str, approval_status: str) -> LeadRecord | None:
        with self._session_factory() as session:
            record = session.scalar(select(LeadRecord).where(LeadRecord.domain == domain))
            if record is None:
                return None
            record.approval_status = approval_status
            session.commit()
            session.refresh(record)
            return record
```
The `session.refresh(record)` call matters: `session.commit()` marks every attribute on `record` as
expired by default, and this method needs to return a record whose attributes remain readable
*after* the `with` block closes the session (the caller in Task 4 reads `.approval_status` on the
returned object). `refresh()` reloads the row immediately, while the session is still open, so the
returned object is fully populated before the session closes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/db/test_repository.py -v`
Expected: PASS (all tests in this file, including the 6 new ones).

- [ ] **Step 5: Run the full test suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `178 passed` (172 + 6 new).

---

### Task 3: `GET /v1/leads` gains an `approval_status` filter

**Files:**
- Modify: `app/api/leads.py`
- Test: `tests/api/test_leads.py`

**Interfaces:**
- Consumes: `LeadRepository.list_leads(..., approval_status=...)` (Task 2).
- Produces: `LeadRecordOut.approval_status: str | None` — consumed by Task 4's endpoints' response
  model (they reuse `LeadRecordOut`).

- [ ] **Step 1: Write the failing test**

In `tests/api/test_leads.py`, update the `_fake_record` helper to accept and pass through
`approval_status` (default `"pending"`, since a fake qualified record with a draft would realistically
have one):
```python
def _fake_record(domain: str, company_name: str, approval_status: str | None = "pending") -> LeadRecord:
    now = datetime.now(timezone.utc)
    return LeadRecord(
        id=1, domain=domain, company_name=company_name, industry="Financial Services",
        status="qualified", score=85, reasoning="Good fit.", summary="A company.",
        key_facts=["fact1"], contacts=[], sources=["https://example.com"],
        outreach_subject="Hi", outreach_body="Hello", approval_status=approval_status,
        first_seen_at=now, last_seen_at=now,
    )
```

Add a new test after `test_list_leads_returns_persisted_records`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/api/test_leads.py -v`
Expected: FAIL — `assert captured["approval_status"] == "pending"` fails with `None == "pending"`
(the route handler doesn't accept/forward the query parameter yet, so the fake repository's
`list_leads` is called with the default `approval_status=None`), and separately
`resp.json()[0]["approval_status"]` raises `KeyError` (the field doesn't exist on `LeadRecordOut`
yet, so FastAPI's response serialization drops it).

- [ ] **Step 3: Implement in `app/api/leads.py`**

Add `approval_status` to `LeadRecordOut` (right after `outreach_body`):
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
    approval_status: str | None
    first_seen_at: datetime
    last_seen_at: datetime
```

Update `list_leads`:
```python
@router.get("/leads", response_model=list[LeadRecordOut])
def list_leads(
    status: Literal["qualified", "disqualified"] | None = None,
    approval_status: Literal["pending", "approved", "rejected", "sent"] | None = None,
    limit: int = 50,
    offset: int = 0,
    settings: Settings = Depends(get_settings),
) -> list[LeadRecordOut]:
    repository = build_lead_repository(settings)
    records = repository.list_leads(
        status=status, approval_status=approval_status, limit=limit, offset=offset
    )
    return [LeadRecordOut.model_validate(r) for r in records]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/api/test_leads.py -v`
Expected: PASS (all tests in this file).

- [ ] **Step 5: Run the full test suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `179 passed` (178 + 1 new).

---

### Task 4: Approval + sent endpoints

**Files:**
- Modify: `app/api/leads.py`
- Test: `tests/api/test_leads.py`

**Interfaces:**
- Consumes: `LeadRepository.get_by_domain`, `LeadRepository.set_approval_status` (Task 2);
  `LeadRecordOut` (Task 3).
- Produces: `POST /v1/leads/{domain}/approval` and `POST /v1/leads/{domain}/sent` — no later task
  in this plan consumes these directly (n8n's workflow JSON in Task 7 calls them as plain HTTP
  routes, not as Python interfaces).

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_leads.py`, after `test_get_lead_returns_404_when_not_found`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/api/test_leads.py -v`
Expected: FAIL with `404 Not Found` (FastAPI's default for an undefined route) on every new test,
since neither endpoint exists yet.

- [ ] **Step 3: Implement in `app/api/leads.py`**

Add, after the existing `get_lead` handler:
```python
class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]


@router.post("/leads/{domain}/approval", response_model=LeadRecordOut)
def decide_lead_approval(
    domain: str, body: ApprovalDecisionRequest, settings: Settings = Depends(get_settings),
) -> LeadRecordOut:
    repository = build_lead_repository(settings)
    record = repository.get_by_domain(domain)
    if record is None:
        raise HTTPException(status_code=404, detail="lead not found")
    if record.approval_status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"lead is not pending approval (current status: {record.approval_status})",
        )
    updated = repository.set_approval_status(domain, body.decision)
    return LeadRecordOut.model_validate(updated)


@router.post("/leads/{domain}/sent", response_model=LeadRecordOut)
def mark_lead_sent(domain: str, settings: Settings = Depends(get_settings)) -> LeadRecordOut:
    repository = build_lead_repository(settings)
    record = repository.get_by_domain(domain)
    if record is None:
        raise HTTPException(status_code=404, detail="lead not found")
    if record.approval_status != "approved":
        raise HTTPException(
            status_code=400,
            detail=f"lead is not approved (current status: {record.approval_status})",
        )
    updated = repository.set_approval_status(domain, "sent")
    return LeadRecordOut.model_validate(updated)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/api/test_leads.py -v`
Expected: PASS (all tests in this file).

- [ ] **Step 5: Run the full test suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `186 passed` (179 + 7 new).

---

### Task 5: `Settings.n8n_alert_webhook_url` + `app/observability/alerting.py`

**Files:**
- Modify: `app/config.py`
- Create: `app/observability/alerting.py`
- Test: `tests/observability/test_alerting.py` (new file)

**Interfaces:**
- Produces: `send_alert(settings: Settings, *, kind: str, status: str, error: str | None) -> None`
  — consumed by Task 6's `JobStore.mark_failed()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/observability/test_alerting.py`:
```python
import httpx

from app.config import Settings
from app.observability.alerting import send_alert


def test_send_alert_is_a_noop_when_webhook_url_unset(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: calls.append((a, kw)))
    settings = Settings(_env_file=None, n8n_alert_webhook_url=None)

    send_alert(settings, kind="lead", status="failed", error="boom")

    assert calls == []


def test_send_alert_posts_to_the_configured_webhook(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout

    monkeypatch.setattr(httpx, "post", fake_post)
    settings = Settings(
        _env_file=None, n8n_alert_webhook_url="https://n8n.example.com/webhook/alert"
    )

    send_alert(settings, kind="discovery", status="failed", error="boom")

    assert captured["url"] == "https://n8n.example.com/webhook/alert"
    assert captured["json"] == {"kind": "discovery", "status": "failed", "error": "boom"}


def test_send_alert_never_raises_when_the_webhook_call_fails(monkeypatch):
    def raising_post(*args, **kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr(httpx, "post", raising_post)
    settings = Settings(
        _env_file=None, n8n_alert_webhook_url="https://n8n.example.com/webhook/alert"
    )

    send_alert(settings, kind="lead", status="failed", error="boom")  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/observability/test_alerting.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.observability.alerting'`.

- [ ] **Step 3: Add the setting in `app/config.py`**

Under the existing `# Observability` section, right after `langfuse_host`:
```python
    # Observability
    langfuse_enabled: bool = True
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None
    n8n_alert_webhook_url: str | None = None
```

- [ ] **Step 4: Create `app/observability/alerting.py`**

```python
import logging

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


def send_alert(settings: Settings, *, kind: str, status: str, error: str | None) -> None:
    if not settings.n8n_alert_webhook_url:
        return
    try:
        httpx.post(
            settings.n8n_alert_webhook_url,
            json={"kind": kind, "status": status, "error": error},
            timeout=5.0,
        )
    except Exception:
        logger.warning("send_alert: failed to reach n8n webhook", exc_info=True)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/observability/test_alerting.py -v`
Expected: PASS (all 3 tests).

- [ ] **Step 6: Run the full test suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `189 passed` (186 + 3 new).

---

### Task 6: `JobStore.mark_failed()` alert wiring

**Files:**
- Modify: `app/api/jobs.py`
- Modify: `app/api/leads.py`
- Test: `tests/api/test_jobs.py`
- Test: `tests/api/test_leads.py`

**Interfaces:**
- Consumes: `send_alert` (Task 5).
- Produces: `JobStore.mark_failed(job_id: str, error: str, settings: Settings | None = None)` — the
  `settings` parameter is optional and keyword-compatible with every pre-existing call.

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_jobs.py` (add `from app.config import Settings` to the imports at the top):
```python
from app.config import Settings


def test_mark_failed_sends_alert_when_settings_configured(monkeypatch):
    import app.api.jobs as jobs_module

    calls = []
    monkeypatch.setattr(jobs_module, "send_alert", lambda settings, **kw: calls.append(kw))

    store = JobStore()
    job = store.create(kind="lead")
    settings = Settings(
        _env_file=None, n8n_alert_webhook_url="https://n8n.example.com/webhook/alert"
    )

    store.mark_failed(job.job_id, "boom", settings)

    assert calls == [{"kind": "lead", "status": "failed", "error": "boom"}]


def test_mark_failed_skips_alert_when_settings_omitted(monkeypatch):
    import app.api.jobs as jobs_module

    calls = []
    monkeypatch.setattr(jobs_module, "send_alert", lambda settings, **kw: calls.append(kw))

    store = JobStore()
    job = store.create(kind="lead")

    store.mark_failed(job.job_id, "boom")  # pre-existing call signature, no settings

    assert calls == []
```

Add to `tests/api/test_leads.py`, after `test_job_is_failed_when_orchestrator_raises`:
```python
def test_job_is_failed_and_alert_is_attempted_when_lead_orchestrator_raises(monkeypatch):
    class _FailingOrchestrator:
        def run(self, target: str) -> Lead:
            raise RuntimeError("boom")

    calls = []
    monkeypatch.setattr(leads_module, "build_lead_orchestrator_agent", lambda settings: _FailingOrchestrator())
    monkeypatch.setattr(leads_module, "build_lead_repository", lambda settings: _FakeRepository())
    monkeypatch.setattr("app.api.jobs.send_alert", lambda settings, **kw: calls.append(kw))

    settings = Settings(
        _env_file=None, n8n_alert_webhook_url="https://n8n.example.com/webhook/alert"
    )
    client = _client_with_overrides(settings, JobStore())
    client.post("/v1/leads", json={"target": "acme.com"})

    assert calls == [{"kind": "lead", "status": "failed", "error": "boom"}]


def test_job_is_failed_and_alert_is_attempted_when_discovery_sweep_raises(monkeypatch):
    def _failing_sweep(settings, queries=None, max_results=None):
        raise RuntimeError("sweep boom")

    calls = []
    monkeypatch.setattr(leads_module, "run_discovery_sweep", _failing_sweep)
    monkeypatch.setattr("app.api.jobs.send_alert", lambda settings, **kw: calls.append(kw))

    settings = Settings(
        _env_file=None, n8n_alert_webhook_url="https://n8n.example.com/webhook/alert"
    )
    client = _client_with_overrides(settings, JobStore())
    client.post("/v1/discovery", json={"query": "credit unions in the UK"})

    assert calls == [{"kind": "discovery", "status": "failed", "error": "sweep boom"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/api/test_jobs.py tests/api/test_leads.py -v`
Expected: FAIL — `TypeError: mark_failed() takes 3 positional arguments but 4 were given` for the
new `test_jobs.py` tests; the two new `test_leads.py` tests fail with `assert [] == [{...}]` (no
alert attempted yet).

- [ ] **Step 3: Implement in `app/api/jobs.py`**

Add the import and update `mark_failed`:
```python
from app.config import Settings
from app.observability.alerting import send_alert
from app.observability.metrics import record_job_outcome
```
```python
    def mark_failed(self, job_id: str, error: str, settings: Settings | None = None) -> None:
        job = self._jobs[job_id]
        job.status = "failed"
        job.error = error
        job.finished_at = datetime.now(timezone.utc)
        record_job_outcome(kind=job.kind, status="failed")
        if settings is not None:
            send_alert(settings, kind=job.kind, status="failed", error=error)
```

- [ ] **Step 4: Wire the call sites in `app/api/leads.py`**

In `_run_lead_job`:
```python
def _run_lead_job(job_store: JobStore, job_id: str, settings: Settings, target: str) -> None:
    job_store.mark_running(job_id)
    try:
        orchestrator = build_lead_orchestrator_agent(settings)
        lead = orchestrator.run(target)
        build_lead_repository(settings).save(lead)
        job_store.mark_done(job_id, lead)
    except Exception as exc:
        job_store.mark_failed(job_id, str(exc), settings)
```

In `_run_discovery_job`:
```python
def _run_discovery_job(
    job_store: JobStore, job_id: str, settings: Settings, queries: list[str],
    max_results: int | None,
) -> None:
    job_store.mark_running(job_id)
    try:
        leads = run_discovery_sweep(settings, queries=queries, max_results=max_results)
        job_store.mark_done(job_id, leads)
    except Exception as exc:
        job_store.mark_failed(job_id, str(exc), settings)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/api/test_jobs.py tests/api/test_leads.py -v`
Expected: PASS (all tests in both files).

- [ ] **Step 6: Run the full test suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `193 passed` (189 + 4 new).

---

### Task 7: n8n workflow JSON files

**Files:**
- Create: `n8n/01-trigger-ingestion.json`
- Create: `n8n/02-approval-and-send.json`
- Create: `n8n/03-alerting.json`
- Test: `tests/test_n8n_workflows.py` (new file)

**Interfaces:** none (these are n8n-native JSON artifacts, not Python interfaces; the HTTP calls
they make target the endpoints from Tasks 3/4, described in each file's own `meta.notes` field).

**Explicit scope reminder:** per this plan's Global Constraints, these files are validated only for
well-formed JSON structure. They are not imported into n8n or executed.

- [ ] **Step 1: Write the failing test**

Create `tests/test_n8n_workflows.py`:
```python
import json
from pathlib import Path

import pytest

N8N_DIR = Path(__file__).resolve().parents[1] / "n8n"


@pytest.mark.parametrize(
    "filename,expected_node_types",
    [
        (
            "01-trigger-ingestion.json",
            {
                "n8n-nodes-base.webhook",
                "n8n-nodes-base.httpRequest",
                "n8n-nodes-base.respondToWebhook",
            },
        ),
        (
            "02-approval-and-send.json",
            {
                "n8n-nodes-base.scheduleTrigger",
                "n8n-nodes-base.webhook",
                "n8n-nodes-base.httpRequest",
                "n8n-nodes-base.slack",
                "n8n-nodes-base.if",
                "n8n-nodes-base.gmail",
            },
        ),
        ("03-alerting.json", {"n8n-nodes-base.webhook", "n8n-nodes-base.slack"}),
    ],
)
def test_n8n_workflow_file_is_well_formed(filename, expected_node_types):
    path = N8N_DIR / filename
    data = json.loads(path.read_text(encoding="utf-8"))

    assert "nodes" in data and isinstance(data["nodes"], list) and data["nodes"]
    assert "connections" in data and isinstance(data["connections"], dict)

    node_types = {node["type"] for node in data["nodes"]}
    assert expected_node_types.issubset(node_types)

    node_names = [node["name"] for node in data["nodes"]]
    assert len(node_names) == len(set(node_names))  # no duplicate node names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_n8n_workflows.py -v`
Expected: FAIL — `FileNotFoundError`, since none of the three JSON files exist yet.

- [ ] **Step 3: Create `n8n/01-trigger-ingestion.json`**

```json
{
  "name": "01 - Trigger Ingestion",
  "nodes": [
    {
      "parameters": {
        "path": "trigger-lead",
        "httpMethod": "POST",
        "responseMode": "responseNode"
      },
      "id": "1a1a1a1a-0001-4000-8000-000000000001",
      "name": "Webhook - Trigger",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 2,
      "position": [240, 300]
    },
    {
      "parameters": {
        "method": "POST",
        "url": "={{ $json.body.query || $json.body.queries ? \"http://app:8000/v1/discovery\" : \"http://app:8000/v1/leads\" }}",
        "sendBody": true,
        "jsonBody": "={{ JSON.stringify($json.body) }}",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "options": {}
      },
      "id": "1a1a1a1a-0002-4000-8000-000000000002",
      "name": "HTTP Request - Call App",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [460, 300]
    },
    {
      "parameters": {
        "respondWith": "allIncomingItems",
        "options": {}
      },
      "id": "1a1a1a1a-0003-4000-8000-000000000003",
      "name": "Respond to Webhook",
      "type": "n8n-nodes-base.respondToWebhook",
      "typeVersion": 1.1,
      "position": [680, 300]
    }
  ],
  "connections": {
    "Webhook - Trigger": {
      "main": [[{ "node": "HTTP Request - Call App", "type": "main", "index": 0 }]]
    },
    "HTTP Request - Call App": {
      "main": [[{ "node": "Respond to Webhook", "type": "main", "index": 0 }]]
    }
  },
  "active": false,
  "settings": { "executionOrder": "v1" },
  "meta": {
    "notes": "Ingestion trigger. POST a JSON body ({\"target\": \"acme.com\"}, or {\"query\": \"...\"} / {\"queries\": [...]}) to this workflow's webhook URL. Routes to POST /v1/discovery when the body has query/queries, otherwise POST /v1/leads. The HTTP Request node needs an httpHeaderAuth credential configured in n8n (header name X-API-Key, value = the app's API_KEY). Not imported or run as part of Phase 10 -- import via n8n's 'Import from File' and wire the credential yourself when ready."
  }
}
```

- [ ] **Step 4: Create `n8n/02-approval-and-send.json`**

```json
{
  "name": "02 - Approval and Send",
  "nodes": [
    {
      "parameters": {
        "rule": { "interval": [{ "field": "minutes", "minutesInterval": 5 }] }
      },
      "id": "2a2a2a2a-0001-4000-8000-000000000001",
      "name": "Schedule - Poll Pending",
      "type": "n8n-nodes-base.scheduleTrigger",
      "typeVersion": 1.2,
      "position": [240, 200]
    },
    {
      "parameters": {
        "method": "GET",
        "url": "http://app:8000/v1/leads",
        "sendQuery": true,
        "queryParameters": {
          "parameters": [{ "name": "approval_status", "value": "pending" }]
        },
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "options": {}
      },
      "id": "2a2a2a2a-0002-4000-8000-000000000002",
      "name": "HTTP Request - Get Pending Leads",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [460, 200]
    },
    {
      "parameters": {
        "resource": "message",
        "operation": "post",
        "channel": "#lead-approvals",
        "text": "=New lead draft awaiting review: *{{ $json.company_name }}* ({{ $json.domain }})\nScore: {{ $json.score }}\nReasoning: {{ $json.reasoning }}\n\nSubject: {{ $json.outreach_subject }}\nBody: {{ $json.outreach_body }}",
        "otherOptions": {}
      },
      "id": "2a2a2a2a-0003-4000-8000-000000000003",
      "name": "Slack - Post Draft With Approve or Reject",
      "type": "n8n-nodes-base.slack",
      "typeVersion": 2.2,
      "position": [680, 200]
    },
    {
      "parameters": {
        "path": "approval-callback",
        "httpMethod": "POST",
        "responseMode": "responseNode"
      },
      "id": "2a2a2a2a-0004-4000-8000-000000000004",
      "name": "Webhook - Slack Button Callback",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 2,
      "position": [240, 500]
    },
    {
      "parameters": {
        "method": "POST",
        "url": "=http://app:8000/v1/leads/{{ $json.body.domain }}/approval",
        "sendBody": true,
        "jsonBody": "={{ JSON.stringify({ decision: $json.body.decision }) }}",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "options": {}
      },
      "id": "2a2a2a2a-0005-4000-8000-000000000005",
      "name": "HTTP Request - Record Decision",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [460, 500]
    },
    {
      "parameters": {
        "conditions": {
          "options": { "caseSensitive": true, "leftValue": "", "typeValidation": "strict" },
          "conditions": [
            {
              "id": "2a2a2a2a-cond-0000",
              "leftValue": "={{ $json.approval_status }}",
              "rightValue": "approved",
              "operator": { "type": "string", "operation": "equals" }
            }
          ],
          "combinator": "and"
        }
      },
      "id": "2a2a2a2a-0006-4000-8000-000000000006",
      "name": "IF - Approved",
      "type": "n8n-nodes-base.if",
      "typeVersion": 2.2,
      "position": [680, 500]
    },
    {
      "parameters": {
        "method": "GET",
        "url": "=http://app:8000/v1/leads/{{ $json.domain }}",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "options": {}
      },
      "id": "2a2a2a2a-0007-4000-8000-000000000007",
      "name": "HTTP Request - Get Full Lead",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [900, 420]
    },
    {
      "parameters": {
        "sendTo": "={{ $json.contacts[0].email }}",
        "subject": "={{ $json.outreach_subject }}",
        "message": "={{ $json.outreach_body }}",
        "options": {}
      },
      "id": "2a2a2a2a-0008-4000-8000-000000000008",
      "name": "Gmail - Send Approved Outreach",
      "type": "n8n-nodes-base.gmail",
      "typeVersion": 2.1,
      "position": [1120, 420]
    },
    {
      "parameters": {
        "method": "POST",
        "url": "=http://app:8000/v1/leads/{{ $json.domain }}/sent",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "options": {}
      },
      "id": "2a2a2a2a-0009-4000-8000-000000000009",
      "name": "HTTP Request - Mark Sent",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [1340, 420]
    }
  ],
  "connections": {
    "Schedule - Poll Pending": {
      "main": [[{ "node": "HTTP Request - Get Pending Leads", "type": "main", "index": 0 }]]
    },
    "HTTP Request - Get Pending Leads": {
      "main": [[{ "node": "Slack - Post Draft With Approve or Reject", "type": "main", "index": 0 }]]
    },
    "Webhook - Slack Button Callback": {
      "main": [[{ "node": "HTTP Request - Record Decision", "type": "main", "index": 0 }]]
    },
    "HTTP Request - Record Decision": {
      "main": [[{ "node": "IF - Approved", "type": "main", "index": 0 }]]
    },
    "IF - Approved": {
      "main": [[{ "node": "HTTP Request - Get Full Lead", "type": "main", "index": 0 }], []]
    },
    "HTTP Request - Get Full Lead": {
      "main": [[{ "node": "Gmail - Send Approved Outreach", "type": "main", "index": 0 }]]
    },
    "Gmail - Send Approved Outreach": {
      "main": [[{ "node": "HTTP Request - Mark Sent", "type": "main", "index": 0 }]]
    }
  },
  "active": false,
  "settings": { "executionOrder": "v1" },
  "meta": {
    "notes": "Two independent trigger chains in one workflow. (1) Schedule Trigger polls GET /v1/leads?approval_status=pending every 5 minutes and posts each draft to Slack with interactive Approve/Reject buttons (configure the buttons' callback URL to this workflow's second webhook path, 'approval-callback', posting {domain, decision}). (2) The Webhook receives that callback, records the decision via POST /v1/leads/{domain}/approval, and on approval fetches the full lead, sends the outreach via Gmail, then marks it sent via POST /v1/leads/{domain}/sent. Requires n8n credentials: httpHeaderAuth (X-API-Key), Slack, Gmail OAuth. Not imported or run as part of Phase 10."
  }
}
```

- [ ] **Step 5: Create `n8n/03-alerting.json`**

```json
{
  "name": "03 - Alerting",
  "nodes": [
    {
      "parameters": {
        "path": "alert",
        "httpMethod": "POST",
        "responseMode": "onReceived"
      },
      "id": "3a3a3a3a-0001-4000-8000-000000000001",
      "name": "Webhook - Receive Alert",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 2,
      "position": [240, 300]
    },
    {
      "parameters": {
        "resource": "message",
        "operation": "post",
        "channel": "#ops-alerts",
        "text": "=Job failed: kind={{ $json.body.kind }} status={{ $json.body.status }} error={{ $json.body.error }}",
        "otherOptions": {}
      },
      "id": "3a3a3a3a-0002-4000-8000-000000000002",
      "name": "Slack - Post Alert",
      "type": "n8n-nodes-base.slack",
      "typeVersion": 2.2,
      "position": [460, 300]
    }
  ],
  "connections": {
    "Webhook - Receive Alert": {
      "main": [[{ "node": "Slack - Post Alert", "type": "main", "index": 0 }]]
    }
  },
  "active": false,
  "settings": { "executionOrder": "v1" },
  "meta": {
    "notes": "Receives the app's push-alert POST (app/observability/alerting.py::send_alert) whenever a background job fails, and posts it to a Slack ops channel. Set N8N_ALERT_WEBHOOK_URL in the app's .env to this workflow's webhook URL once imported and activated. Requires a Slack credential. Not imported or run as part of Phase 10."
  }
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_n8n_workflows.py -v`
Expected: PASS (3 parametrized cases).

- [ ] **Step 7: Run the full test suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `196 passed` (193 + 3 new).

---

### Task 8: `n8n` service in `deploy/docker-compose.yml`

**Files:**
- Modify: `deploy/docker-compose.yml`
- Test: `tests/test_docker_compose.py` (new file)

**Interfaces:** none (infra config, not a Python interface).

- [ ] **Step 1: Write the failing test**

Create `tests/test_docker_compose.py`:
```python
from pathlib import Path

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "deploy" / "docker-compose.yml"


def test_docker_compose_includes_n8n_service():
    content = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "\n  n8n:\n" in content
    assert "image: n8nio/n8n" in content
    assert '"5678:5678"' in content
    assert "n8n_data:/home/node/.n8n" in content
    assert "n8n_data:" in content.split("\nvolumes:")[-1]  # declared as a top-level named volume
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_docker_compose.py -v`
Expected: FAIL — none of the asserted strings are present in the current file yet.

- [ ] **Step 3: Update `deploy/docker-compose.yml`**

Full file, with the new `n8n` service and its volume added:
```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: leads
      POSTGRES_PASSWORD: leads
      POSTGRES_DB: leads
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U leads"]
      interval: 5s
      timeout: 3s
      retries: 5

  app:
    build:
      context: ..
      dockerfile: Dockerfile
    environment:
      DATABASE_URL: postgresql+psycopg://leads:leads@db:5432/leads
    ports:
      - "8000:8000"
    depends_on:
      db:
        condition: service_healthy

  n8n:
    image: n8nio/n8n
    ports:
      - "5678:5678"
    volumes:
      - n8n_data:/home/node/.n8n

volumes:
  pgdata:
  n8n_data:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_docker_compose.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `197 passed` (196 + 1 new).

---

### Task 9: Learning guide + index updates

**Files:**
- Create: `docs/learning/phase-10-n8n-integration.md`
- Modify: `docs/learning/README.md`
- Modify: `README.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Write `docs/learning/phase-10-n8n-integration.md`** — same structure as the Phase
  1-9 guides (see `docs/learning/phase-9-observability.md` for the established format: What & why /
  The flow / File-by-file walkthrough / Key concepts table / How to run & test / What's next). Must
  cover:
  - **What & why** — why n8n orchestrates the existing Python agent rather than a native n8n AI
    Agent node (showcases operationalizing a real custom multi-agent system, not rebuilding it in
    a low-code tool); why `approval_status` is a column on `leads` rather than a separate table
    (one source of truth, no join for a simple state machine); why the pending-on-first-save-only
    rule exists (a later re-save of an already-decided lead must never silently reset a human's
    decision back to "pending"); why alerting is push (app calls n8n) rather than pull (n8n polling
    metrics) — immediate, no counter-delta logic needed in n8n; explicitly note the phase's scope
    boundary — the n8n workflows are hand-authored, importable JSON, but were not live-tested or
    run as part of this phase (per explicit user direction), so credential setup and end-to-end
    verification are the user's own next step whenever they choose to do it.
  - **The flow** — an ASCII diagram covering: (1) the trigger webhook -> `POST /v1/leads` or
    `/v1/discovery`; (2) the approval+send workflow's two independent chains (Schedule Trigger ->
    poll pending -> Slack post with buttons; Webhook -> Slack callback -> `POST .../approval` -> IF
    approved -> `GET` full lead -> Gmail send -> `POST .../sent`); (3) the alerting push (app's
    `send_alert()` -> n8n webhook -> Slack).
  - **File-by-file walkthrough** — `app/db/models.py`/the new Alembic migration (the
    `approval_status` column); `app/db/repository.py` (`save()`'s pending-on-first-save-only logic,
    `list_leads()`'s new filter, `set_approval_status()` and why it calls `session.refresh()` before
    returning); `app/api/leads.py` (the two new endpoints, their 404/400 error conditions);
    `app/observability/alerting.py` (`send_alert()`'s no-op-when-unset + never-raises design,
    consistent with Phase 9's `traced_span` no-op pattern); `app/api/jobs.py` (`mark_failed()`'s new
    optional `settings` parameter, backward-compatible with every existing call); the `n8n/`
    directory's three workflow files and what each does; `deploy/docker-compose.yml`'s new `n8n`
    service.
  - **Key concepts table** — approval/state-machine-as-a-column, push vs. pull for
    cross-system notifications, "never overwrite a human decision" idempotency in an upsert,
    orchestration-layer vs. application-layer separation of concerns (n8n handles routing/humans/
    sending; the app handles research/qualification/drafting — neither duplicates the other).
  - **How to run & test** — `pytest tests/db/test_models.py tests/db/test_repository.py
    tests/api/test_leads.py tests/api/test_jobs.py tests/observability/test_alerting.py
    tests/test_n8n_workflows.py tests/test_docker_compose.py -v`; how to bring up the full stack
    (`docker compose -f deploy/docker-compose.yml up`, now including `n8n` on `localhost:5678`);
    an honest note that actually importing the workflows, configuring Slack/Gmail credentials, and
    running an end-to-end approval is a manual next step this phase deliberately didn't do.
  - **What's next** — Phase 11: Deploy, simplified to docker-compose only (setup scripts +
    deployment docs in the repo); after that, the project concludes with a single comprehensive
    documentation page covering the whole build, published as a shareable link.

- [ ] **Step 2: Update `docs/learning/README.md`** — add a row to the phase-guides table:

```markdown
| [Phase 10 — n8n Integration](phase-10-n8n-integration.md) | n8n orchestrates the existing Python agent through three workflows — webhook-triggered ingestion, a Slack human-approval gate with Gmail send, and push-based alerting on job failure — via a new `approval_status` state machine on the `leads` table and two new API endpoints. |
```

And update the mental-model diagram's Phase 10 line (currently reads
`Phase 10 n8n .................. ingestion, human-approval sending, alerting`):

```
Phase 10 n8n .................. ingestion, human-approval sending, alerting (built, not yet run)
```

- [ ] **Step 3: Update `README.md`** — change the Phase 10 status checkbox from `[ ]` to `[x]` (it
  currently reads `- [ ] Phase 10 — n8n integration (ingestion, human-approval sending, alerting)`)
  and update the "Current" marker (currently `**Current: Phase 9 — Observability** ✅`):

```markdown
- [x] Phase 10 — n8n integration (approval-status state machine, 2 new API endpoints, push alerting, 3 n8n workflows -- built, not yet run/tested)
```

```markdown
**Current: Phase 10 — n8n Integration** ✅
```

  Also add the matching entry to the "Documentation" bullet list (after the Phase 9 link):

```markdown
  - [Phase 10 — n8n Integration](docs/learning/phase-10-n8n-integration.md)
```

- [ ] **Step 4: Report changes** to the user for review/commit.

---

## Phase 10 Definition of Done

- `./.venv/Scripts/python.exe -m pytest -q` → all green (Phase 1-10), **197 passed**, no network
  call to n8n, Slack, Gmail, or any real credential required anywhere.
- `GET /v1/leads?approval_status=pending` returns only leads awaiting review; `POST
  /v1/leads/{domain}/approval` and `POST /v1/leads/{domain}/sent` correctly transition
  `approval_status` with the right 400/404 error paths.
- A job failure calls `send_alert()`, which no-ops when `N8N_ALERT_WEBHOOK_URL` is unset and never
  raises when the webhook call itself fails.
- Three well-formed n8n workflow JSON files exist in `n8n/`, each structurally validated by a test,
  none of them imported into a running n8n instance or executed.
- `deploy/docker-compose.yml` includes a runnable (but not yet run, per scope) `n8n` service.
- Learning guide written; README + learning index updated, explicitly noting the workflows are
  built but not live-tested.

**Next phase:** Phase 11 — Deploy, simplified to docker-compose only. After that, the project
concludes with a single comprehensive documentation page (published as a shareable link) covering
the whole implementation — replacing the originally-planned Phase 12 (Quality + polish) entirely.
