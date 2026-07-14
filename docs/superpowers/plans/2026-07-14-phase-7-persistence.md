# Phase 7: Persistence — Implementation Plan

> **Execution note:** The user commits/pushes to GitHub themselves. Do **not** run `git commit`
> or `git push`. End each task by reporting exactly what changed for the user to review and commit.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the pipeline durable memory across separate process runs. Two things, tightly
coupled: (1) every `Lead` produced gets written to Postgres, and (2) Discovery uses that same
storage to stop re-surfacing domains it has already researched.

**Why these two things belong together:** the dedup problem the user noticed ("the search is
giving the same domain again") only *has* a fix once there's a persistent record of what's been
seen before. There's no in-memory way to remember across two separate `python scripts/try_discovery.py`
invocations — the process exits and takes its memory with it. Postgres is that memory.

**Scope decision (narrowed from the original design spec):** the original spec's data-model table
listed `leads`, `contacts`, `research_briefs`, `outreach_drafts`, `agent_runs`, `request_logs`,
`enrichment_cache` as separate tables. This phase builds only **one `leads` table**, with nested
data (`contacts`, `key_facts`, `sources`) stored as JSON columns rather than normalized into their
own tables. `agent_runs` / `request_logs` are audit/observability concerns that fit Phase 10
(Observability) better — building them now would be scope creep with no consumer yet.

**Tech Stack:** SQLAlchemy 2.0 (already a dependency), `psycopg[binary]` (already a dependency),
Alembic (new dependency, for migrations). Tests use an in-memory SQLite engine — no real Postgres
required to run the suite, consistent with every prior phase's "no network in tests" rule.

## Global Constraints

- **Python:** 3.12+.
- **No network/real DB in tests:** every repository test uses `create_engine("sqlite:///:memory:")`
  + `Base.metadata.create_all(engine)` — never the real configured `DATABASE_URL`.
- **`LeadRepository` takes an injected `session_factory`** (same DI pattern as every other
  collaborator in this project) — never constructs its own engine internally.
- **Upsert by `domain`**: saving a `Lead` whose domain already exists in the table updates that row
  (refreshing `last_seen_at`) rather than creating a duplicate row. `first_seen_at` is set once and
  never overwritten.
- **Dedup is permanent, not time-based** (per the user's explicit choice): once a domain exists in
  the `leads` table at all, it is skipped by Discovery for good — there is no re-processing window.
  This is gated by `DISCOVERY_SKIP_SEEN_DOMAINS` (default `true`), per the user's "make it a
  setting" ask — set it to `false` to disable the skip entirely and let Discovery re-process
  everything every time.
- **Persistence and dedup are separate concerns in code**: `discover_and_qualify_leads()` keeps
  working exactly as it does today when called without a `repository` (existing tests untouched);
  the real entry point (`run_discovery_pipeline`) is the only thing that wires a real repository in.
- **Alembic from the start** (per the user's choice), even though there's only one table today —
  this is the seam that lets the schema evolve later without hand-editing production tables.
- **Every task ends** with: tests green, then report the changes to the user for review/commit.

## File Structure

```
app/
  db/
    __init__.py
    session.py        # unchanged -- existing get_engine() singleton stays scoped to /ready only
    models.py          # Base, LeadRecord (the `leads` table)
    repository.py       # LeadRepository (save / filter_unseen) + build_lead_repository(settings)
  agents/
    discovery_pipeline.py   # + repository/dedup wiring
  config.py             # + discovery_skip_seen_domains, new database_url default
alembic/
  env.py
  script.py.mako
  versions/
    <rev>_create_leads_table.py
alembic.ini
scripts/
  try_lead.py            # + persist the single Lead (real-run branch only)
tests/
  db/
    __init__.py
    test_repository.py
  agents/
    test_discovery_pipeline.py   # + dedup/persistence tests
docs/
  learning/phase-7-persistence.md
```

---

### Task 1: `LeadRecord` model + `LeadRepository`

**Files:**
- Modify: `pyproject.toml` (add `"alembic>=1.13"` to `dependencies`)
- Create: `app/db/models.py`, `app/db/repository.py`
- Test: `tests/db/__init__.py` (empty), `tests/db/test_repository.py`

**Correction made during implementation:** the original draft of this task added a
`get_session_factory()` singleton to `app/db/session.py` and had `build_lead_repository(settings)`
call it. That was a real DI bug: `get_session_factory()`/`get_engine()` are `@lru_cache`d with *no
arguments* and internally call the app-wide `get_settings()` — so `build_lead_repository(settings)`
would silently ignore whatever `settings` object was actually passed to it and always connect using
the global cached settings instead (dangerous: two pre-existing Discovery-pipeline tests that inject
fake collaborators via `monkeypatch` don't touch `build_lead_repository`, so they'd have started
making a **real** Postgres connection attempt the moment this shipped). Fixed by leaving
`app/db/session.py` untouched (its `get_engine()` singleton stays exactly as Phase 1 built it,
still used only by the `/ready` health check) and having `build_lead_repository(settings)` build its
own `create_engine(settings.database_url, ...)` directly — the same "construct straight from the
settings you were given" pattern every other `build_x(settings)` factory in this codebase already
follows.

**Interfaces:**
- Consumes: `Lead`, `Qualification`, `OutreachDraft` (`app.schemas.lead`); `Contact`, `ResearchBrief`
  (`app.schemas.research`); `Candidate` (`app.schemas.discovery`).
- Produces:
  - `Base` (SQLAlchemy `DeclarativeBase`), `LeadRecord` (table `leads`): `id`, `domain` (unique,
    indexed), `company_name`, `industry`, `status`, `score`, `reasoning`, `summary`,
    `key_facts` (JSON), `contacts` (JSON), `sources` (JSON), `outreach_subject`, `outreach_body`,
    `first_seen_at`, `last_seen_at`.
  - `LeadRepository(session_factory)`:
    - `.save(lead: Lead) -> None` — upsert by `lead.research.domain`; no-ops if `domain` is `None`.
    - `.filter_unseen(candidates: list[Candidate]) -> list[Candidate]` — returns only candidates
      whose domain does **not** already exist anywhere in the table (permanent exclusion, no time
      window).
  - `build_lead_repository(settings) -> LeadRepository` — wires a real Postgres-backed repository
    from `settings.database_url`.

- [ ] **Step 1: Add the dependency** — in `pyproject.toml`, add `"alembic>=1.13"` to `dependencies`.

- [ ] **Step 2: Install it**

Run: `./.venv/Scripts/python.exe -m pip install -q -e "."`
Expected: installs `alembic`; exit 0.

- [ ] **Step 3: Write the failing test** — `tests/db/test_repository.py`

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, LeadRecord
from app.db.repository import LeadRepository
from app.schemas.discovery import Candidate
from app.schemas.lead import Lead, OutreachDraft, Qualification
from app.schemas.research import Contact, ResearchBrief


def _repository():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return LeadRepository(sessionmaker(bind=engine)), sessionmaker(bind=engine)


def _lead(domain: str, company_name: str = "Acme") -> Lead:
    return Lead(
        research=ResearchBrief(
            company_name=company_name,
            domain=domain,
            industry="Financial Services",
            summary="A company.",
            key_facts=["Founded 1990"],
            contacts=[Contact(name="Jane Doe", role="CTO", email="jane@example.com")],
            sources=["https://example.com"],
        ),
        qualification=Qualification(score=85, reasoning="Strong fit."),
        outreach=OutreachDraft(subject="Hi", body="Hello there"),
        status="qualified",
    )


def test_save_creates_a_new_row():
    repo, session_factory = _repository()
    repo.save(_lead("acme.com"))

    with session_factory() as session:
        record = session.query(LeadRecord).filter_by(domain="acme.com").one()
        assert record.company_name == "Acme"
        assert record.score == 85
        assert record.key_facts == ["Founded 1990"]
        assert record.outreach_subject == "Hi"
        assert record.first_seen_at is not None
        assert record.last_seen_at is not None


def test_save_upserts_on_repeat_domain_and_keeps_first_seen_at():
    repo, session_factory = _repository()
    repo.save(_lead("acme.com", company_name="Acme"))
    with session_factory() as session:
        first_seen = session.query(LeadRecord).filter_by(domain="acme.com").one().first_seen_at

    repo.save(_lead("acme.com", company_name="Acme Renamed"))

    with session_factory() as session:
        records = session.query(LeadRecord).filter_by(domain="acme.com").all()
        assert len(records) == 1
        assert records[0].company_name == "Acme Renamed"
        assert records[0].first_seen_at == first_seen


def test_save_skips_leads_with_no_domain():
    repo, session_factory = _repository()
    lead = Lead(
        research=ResearchBrief(company_name="No Domain Co", summary="n/a"),
        qualification=Qualification(score=10, reasoning="no domain"),
        status="disqualified",
    )
    repo.save(lead)

    with session_factory() as session:
        assert session.query(LeadRecord).count() == 0


def test_filter_unseen_excludes_any_previously_seen_domain():
    repo, session_factory = _repository()
    repo.save(_lead("acme.com"))

    candidates = [Candidate(name="Acme", domain="acme.com"), Candidate(name="Beta", domain="beta.com")]
    unseen = repo.filter_unseen(candidates)

    assert [c.domain for c in unseen] == ["beta.com"]


def test_filter_unseen_excludes_regardless_of_how_long_ago_it_was_seen():
    repo, session_factory = _repository()
    repo.save(_lead("acme.com"))
    with session_factory() as session:
        record = session.query(LeadRecord).filter_by(domain="acme.com").one()
        record.last_seen_at = record.last_seen_at.replace(year=2000)  # ages it artificially
        session.commit()

    candidates = [Candidate(name="Acme", domain="acme.com")]
    unseen = repo.filter_unseen(candidates)

    assert unseen == []  # still excluded -- dedup is permanent, not time-windowed


def test_filter_unseen_with_empty_candidates_returns_empty():
    repo, _ = _repository()
    assert repo.filter_unseen([]) == []
```

- [ ] **Step 4: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/db/test_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.db.models'`.

- [ ] **Step 5: Create `app/db/models.py`**

```python
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class LeadRecord(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    company_name: Mapped[str] = mapped_column(String(255))
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    score: Mapped[int] = mapped_column(Integer)
    reasoning: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    key_facts: Mapped[list] = mapped_column(JSON, default=list)
    contacts: Mapped[list] = mapped_column(JSON, default=list)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    outreach_subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    outreach_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 6: Create `app/db/repository.py`**

```python
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.models import LeadRecord
from app.schemas.discovery import Candidate
from app.schemas.lead import Lead


class LeadRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

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
            record.last_seen_at = now
            session.commit()

    def filter_unseen(self, candidates: list[Candidate]) -> list[Candidate]:
        """Returns only candidates whose domain has never been saved before (permanent dedup)."""
        if not candidates:
            return []

        domains = [c.domain for c in candidates]
        with self._session_factory() as session:
            seen = set(
                session.scalars(select(LeadRecord.domain).where(LeadRecord.domain.in_(domains)))
            )
        return [c for c in candidates if c.domain not in seen]


def build_lead_repository(settings) -> LeadRepository:
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    return LeadRepository(sessionmaker(bind=engine))
```

- [ ] **Step 7: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/db/test_repository.py -v`
Expected: PASS (6 passed).

- [ ] **Step 8: Report changes** to the user for review/commit.

---

### Task 2: Wire dedup + persistence into the Discovery pipeline

**Files:**
- Modify: `app/config.py` (add `discovery_skip_seen_domains`, update `database_url` default)
- Modify: `app/agents/discovery_pipeline.py`
- Modify: `scripts/try_lead.py` (persist the single Lead in the real-run branch)
- Test: `tests/test_config.py`, `tests/agents/test_discovery_pipeline.py`

**Interfaces:**
- Consumes: `LeadRepository`, `build_lead_repository` (Task 1).
- Produces: `discover_and_qualify_leads(lead_source, orchestrator, query, max_results,
  repository=None, skip_seen_domains=False) -> list[Lead]` (backward-compatible — existing
  positional-call tests keep passing unchanged); `run_discovery_pipeline` now wires a real
  `LeadRepository` and passes `settings.discovery_skip_seen_domains` through.

- [ ] **Step 1: Add config field** — in `app/config.py`, after the `discovery_max_results` line:

```python
    # Discovery dedup -- once a domain is in the leads table, skip re-processing it (permanent)
    discovery_skip_seen_domains: bool = True
```

And update the `database_url` default to point at a local (non-docker-compose) Postgres:

```python
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/leadgen"
```

- [ ] **Step 2: Add config tests** — append to `tests/test_config.py`:

```python
def test_discovery_skip_seen_domains_default():
    s = Settings(_env_file=None)
    assert s.discovery_skip_seen_domains is True


def test_discovery_skip_seen_domains_env_override(monkeypatch):
    monkeypatch.setenv("DISCOVERY_SKIP_SEEN_DOMAINS", "false")
    s = Settings(_env_file=None)
    assert s.discovery_skip_seen_domains is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError` on the new field.

- [ ] **Step 4: Update `app/agents/discovery_pipeline.py`**

```python
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
    candidates = lead_source.discover(query, max_results)

    if repository is not None and skip_seen_domains:
        candidates = repository.filter_unseen(candidates)

    leads = [orchestrator.run(candidate.domain) for candidate in candidates]

    if repository is not None:
        for lead in leads:
            repository.save(lead)

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
```

- [ ] **Step 5: Update `tests/agents/test_discovery_pipeline.py`**. Two changes to the *existing*
  tests are required, not just new tests appended:
  - `_lead_for(target)` must set `domain=target` on the fake `ResearchBrief` — without it,
    `LeadRepository.save()` correctly no-ops (by design: leads with no domain aren't persisted),
    which would silently zero out the new persistence tests below.
  - `test_run_discovery_pipeline_uses_settings_default_max_results` and
    `test_run_discovery_pipeline_explicit_max_results_overrides_settings` must now also monkeypatch
    `build_lead_repository` — `run_discovery_pipeline` always builds a real repository, so without
    this these two tests would attempt a real Postgres connection.

  Add near the top of the file (helpers used by both old and new tests):

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, LeadRecord
from app.db.repository import LeadRepository


def _in_memory_repository():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return LeadRepository(sessionmaker(bind=engine)), sessionmaker(bind=engine)


def _seed(session_factory, domain: str) -> None:
    from datetime import datetime, timezone

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
```

  Update `_lead_for` (used by `_FakeOrchestrator`) to set `domain`:

```python
def _lead_for(target: str) -> Lead:
    return Lead(
        research=ResearchBrief(company_name=target, domain=target, summary=f"Summary for {target}"),
        qualification=Qualification(score=80, reasoning="ok"),
        status="qualified",
    )
```

  Update the two pre-existing `run_discovery_pipeline` tests to also monkeypatch
  `build_lead_repository` (with an in-memory fake, so no real DB is touched):

```python
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
```

  Then add the new dedup/persistence tests:

```python
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
```

(`Candidate` is already imported at the top of the file — no new import needed there.)

- [ ] **Step 6: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_config.py tests/agents/test_discovery_pipeline.py -v`
Expected: PASS (all new + existing tests green).

- [ ] **Step 7: Persist the single Lead in `scripts/try_lead.py`** — in the `has_key and not
  force_demo` branch only (real runs), right after `lead = agent.run(target)` and before the
  exporter loop, add:

```python
    if has_key and not force_demo:
        from app.db.repository import build_lead_repository

        build_lead_repository(settings).save(lead)
        print("\nPersisted to Postgres.")
```

- [ ] **Step 8: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all Phase 1-7 tests green (90 prior + this phase's new tests), no network/DB required.

- [ ] **Step 9: Report changes** to the user for review/commit.

---

### Task 3: Alembic migration for the `leads` table

**Files:**
- Create: `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/<rev>_create_leads_table.py`

**Interfaces:** none new in `app/` — this task only adds migration tooling/files.

- [ ] **Step 1: Initialize Alembic**

Run: `./.venv/Scripts/python.exe -m alembic init alembic`
Expected: creates `alembic.ini` and `alembic/` directory with `env.py`, `script.py.mako`,
`versions/`.

- [ ] **Step 2: Point `alembic.ini` at the app's database URL** — leave `sqlalchemy.url` blank in
  `alembic.ini` and set it from `env.py` instead (keeps one source of truth: `Settings`).

- [ ] **Step 3: Wire `alembic/env.py` to `app.db.models.Base` and `Settings.database_url`** — edit
  the generated `env.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db.models import Base

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata
```

(Insert these lines near the top of the generated `env.py`, replacing its `target_metadata = None`
line and adding the `sqlalchemy.url` override before `run_migrations_offline`/`run_migrations_online`
are called.)

- [ ] **Step 4: Autogenerate the migration**

Run: `./.venv/Scripts/python.exe -m alembic revision --autogenerate -m "create leads table"`
Expected: creates `alembic/versions/<rev>_create_leads_table.py` with `op.create_table("leads", ...)`
matching `LeadRecord`.

- [ ] **Step 5: Report changes** to the user for review/commit — the migration file itself is not
  applied yet (that requires a reachable Postgres server; see Task 4).

---

### Task 4: Real Postgres verification (requires the user's local server running)

**Files:** none (verification only).

- [ ] **Step 1: Confirm the local PostgreSQL server is running** and reachable at
  `localhost:5432` with user `postgres` / password `postgres` (the defaults assumed in Task 2's
  `database_url`). If different, update `DATABASE_URL` in `.env` accordingly.

- [ ] **Step 2: Create the `leadgen` database** (one-time):

Run: `./.venv/Scripts/python.exe -c "import psycopg; c = psycopg.connect('postgresql://postgres:postgres@localhost:5432/postgres', autocommit=True); c.execute('CREATE DATABASE leadgen'); print('created')"`
Expected: `created` (or a clear "already exists" error, which is fine to ignore).

- [ ] **Step 3: Apply the migration**

Run: `./.venv/Scripts/python.exe -m alembic upgrade head`
Expected: creates the real `leads` table in the `leadgen` database.

- [ ] **Step 4: Run a real Discovery pass twice with the same query** and confirm the second run
  finds zero new candidates, proving permanent dedup works end-to-end:

Run: `./.venv/Scripts/python.exe scripts/try_discovery.py "credit unions in the UK"`
Run it again immediately after.
Expected: second run reports `0 LEAD(S) FOUND` (or only genuinely new domains not seen in run one),
because every domain from the first run is now permanently recorded in `leads`.

- [ ] **Step 5: Report the result** to the user.

---

### Task 5: Learning guide + index updates

**Files:**
- Create: `docs/learning/phase-7-persistence.md`
- Modify: `docs/learning/README.md`
- Modify: `README.md` (Status section)

**Interfaces:** none (documentation only).

- [ ] **Step 1: Write `docs/learning/phase-7-persistence.md`** — same structure as prior guides.
  Must cover:
  - **What & why** — the dedup problem the user found, and why it can only be solved with
    something that outlives a single process run; why one `leads` table with JSON columns rather
    than the original spec's full normalized table set (no consumer yet for `agent_runs`/
    `request_logs` — that's Phase 10); why dedup is permanent by default rather than
    time-windowed — the user's explicit choice, with `DISCOVERY_SKIP_SEEN_DOMAINS=false` as the
    escape hatch if they ever want full re-processing.
  - **The flow** — `run_discovery_pipeline -> lead_source.discover() -> repository.filter_unseen()
    (if enabled) -> orchestrator.run() per remaining candidate -> repository.save() each Lead ->
    return leads`.
  - **File-by-file walkthrough** — `app/db/models.py` (the `LeadRecord` table, JSON columns for
    nested data); `app/db/repository.py` (`save` as upsert-by-domain, `filter_unseen` as the
    permanent-exclusion query); `app/db/session.py` (`get_engine`/`get_session_factory`, both
    `lru_cache`d singletons); `alembic/` (why migrations from day one even for a single table — the
    schema will grow).
  - **Key concepts table** — upsert-by-natural-key, dependency-injected `session_factory` for
    testability (SQLite in tests, Postgres in production, same code path), permanent dedup vs.
    time-windowed (and why permanent was chosen here), migrations-from-day-one.
  - **How to run & test** — `pytest tests/db tests/agents/test_discovery_pipeline.py -v`, explaining
    what each test proves; `alembic upgrade head` against a real local Postgres; running
    `scripts/try_discovery.py` twice back-to-back to see dedup kick in (second run finds 0 new).
  - **What's next** — Phase 8: API layer (FastAPI endpoints exposing discovery/lead endpoints,
    reading from/writing to this same `leads` table).

- [ ] **Step 2: Update `docs/learning/README.md`** — add a row to the phase-guides table:

```markdown
| [Phase 7 — Persistence](phase-7-persistence.md) | Durable memory across process runs: every `Lead` is upserted into a Postgres `leads` table by domain, and Discovery uses that same table to permanently skip domains it has already researched (configurable). |
```

- [ ] **Step 3: Update `README.md`** — change the Phase 7 status line and the "Current" marker:

```markdown
- [x] Phase 7 — Persistence (Postgres `leads` table via Alembic; permanent domain dedup for Discovery)
```

- [ ] **Step 4: Report changes** to the user for review/commit.

---

## Phase 7 Definition of Done

- `./.venv/Scripts/python.exe -m pytest -q` → all green (Phase 1-7), no network or real Postgres
  required.
- `LeadRepository.save()` upserts by domain; `filter_unseen()` permanently excludes any domain ever
  saved, regardless of how long ago.
- `discover_and_qualify_leads()` remains fully backward-compatible when called without a repository
  (existing Phase 5 tests untouched).
- Alembic migration exists and (once the user's local Postgres is confirmed reachable) has been
  applied to a real `leadgen` database.
- A real back-to-back `try_discovery.py` run demonstrates zero repeated domains on the second pass.
- Learning guide written; README + learning index updated.

**Next phase (planned just-in-time after this one):** Phase 8 — API layer (FastAPI endpoints
exposing the discovery/lead pipeline as a real service, backed by this same `leads` table).
