# Phase 7 — Persistence (Learning Guide)

> **Goal of this phase:** give the pipeline memory that outlives a single process run — every
> `Lead` gets written to Postgres, and Discovery uses that same storage to stop re-surfacing
> companies it has already found.

---

## 1. What & why

Every phase before this one produced genuinely useful data that vanished the moment the script
exited. Worse, that statelessness caused a real, user-visible bug: running
`scripts/try_discovery.py "credit unions in the UK"` twice in a row would surface the *same*
companies both times — the search results are similar run to run, and nothing remembered that
they'd already been researched. There's no way to fix "don't show me what I've already seen" without
something that survives past the process. That something is a `leads` table in Postgres.

**Why one `leads` table with JSON columns, not the original spec's full table set.** The original
design spec's data model listed `leads`, `contacts`, `research_briefs`, `outreach_drafts`,
`agent_runs`, `request_logs`, and `enrichment_cache` as separate normalized tables. Building all of
that now would be scope creep — `agent_runs` and `request_logs` are audit/observability concerns
with no consumer yet (that's Phase 10). `contacts`, `key_facts`, and `sources` are nested lists that
only ever get read back out whole (never queried by their internal fields), so a JSON column is the
right level of normalization: reachable, but not artificially split across tables nobody joins
against yet.

**Why dedup is permanent, not time-windowed.** An earlier draft of this phase considered a
"re-eligible after N days" rule (things change; a company's fit can change too). The user chose the
simpler rule instead: once a domain is in the `leads` table, it's done — skip it for good. The
escape hatch is `DISCOVERY_SKIP_SEEN_DOMAINS=false`, which turns dedup off entirely rather than
adjusting a window.

---

## 2. The flow

```
  run_discovery_pipeline(settings, query)
     │
     ▼
  lead_source.discover(query, max_results)          -- same as Phase 5, unchanged
     │
     ▼
  repository.filter_unseen(candidates)               -- NEW: only if DISCOVERY_SKIP_SEEN_DOMAINS
     drops any candidate whose domain already exists in `leads`, permanently
     │
     ▼
  for each remaining candidate: orchestrator.run(candidate.domain)   -- same as Phase 5, unchanged
     │
     ▼
  for each resulting Lead: repository.save(lead)     -- NEW: upsert by domain into Postgres
     │
     ▼
  return leads
```

`discover_and_qualify_leads()` itself didn't need to change its core loop — `repository` and
`skip_seen_domains` are optional parameters that default to "off," so every Phase 5 test that calls
it without a repository keeps working exactly as before. Only `run_discovery_pipeline()` (the real
entry point) always wires a live repository in.

---

## 3. File-by-file walkthrough

### `app/db/models.py` — `LeadRecord`
One SQLAlchemy table, `leads`, mirroring the `Lead` schema flattened: `domain` (unique + indexed —
this is the natural key the whole phase revolves around), `company_name`, `industry`, `status`,
`score`, `reasoning`, `summary`, then `key_facts` / `contacts` / `sources` as `JSON` columns (each
one round-trips as a Python list/dict, no separate tables needed), `outreach_subject` /
`outreach_body` (nullable — disqualified leads have none), and `first_seen_at` / `last_seen_at`
(the audit trail: when a domain first appeared, and when it was last (re-)saved).

### `app/db/repository.py` — `LeadRepository`
- **`save(lead)` is an upsert by domain, not a blind insert.** Look up an existing row by
  `lead.research.domain`; if found, update it in place and leave `first_seen_at` untouched; if not,
  create a new row with `first_seen_at = now`. A lead with no `domain` at all silently no-ops —
  there's nothing to key it by, and inventing one would be worse than skipping it.
- **`filter_unseen(candidates)` is the whole dedup mechanism**, in one query: select every domain in
  the candidate list that already exists in `leads`, then return only the candidates *not* in that
  set. No date comparison at all — existence alone is enough, which is what makes the dedup
  permanent.
- **`build_lead_repository(settings)` builds its own engine directly from `settings.database_url`**,
  rather than reusing `app/db/session.py`'s `get_engine()` singleton. This mattered in practice: an
  earlier version of this function called the app-wide cached `get_engine()`/`get_settings()`
  instead, which meant `build_lead_repository(some_settings)` would silently ignore whatever
  `some_settings` object was actually passed in and always connect using the one global cached
  settings object. Two pre-existing tests that inject fake collaborators via `monkeypatch` don't
  touch `build_lead_repository`, so that bug would have made them attempt a real Postgres connection
  the moment persistence shipped. The fix: construct the engine straight from the `settings`
  argument, same as every other `build_x(settings)` factory in this codebase already does.

### `app/agents/discovery_pipeline.py` — save-as-you-go, skip-on-failure
A bug surfaced shortly after this phase shipped: the batch loop originally built the whole
`leads` list via `[orchestrator.run(c.domain) for c in candidates]` and only called
`repository.save()` *after* that finished. If any single candidate's orchestrator call raised
(e.g. a flaky free LLM model returning malformed JSON for the outreach draft), the exception
aborted the whole list comprehension — silently discarding every already-successfully-processed
lead earlier in that same batch, with nothing persisted and nothing returned. Fixed by looping
explicitly: each candidate is processed and saved immediately, and a candidate whose orchestrator
call raises is logged and skipped (`continue`) rather than crashing the whole run. One bad
candidate now costs exactly one lead, not the whole batch.

### Tests use SQLite, production uses Postgres — same code path
`LeadRepository` takes an injected `session_factory` (a plain SQLAlchemy `sessionmaker`), never
constructs one itself. Tests bind it to `create_engine("sqlite:///:memory:")` +
`Base.metadata.create_all(engine)`; `build_lead_repository(settings)` binds it to the real
`settings.database_url` (Postgres). The repository code itself never knows or cares which one it's
talking to.

### `run_discovery_sweep()` — multiple queries, one shared repository
A single query like `"credit unions in the UK"` only covers so much ground. `DISCOVERY_QUERIES`
(comma list) lets one run sweep several queries in a row (e.g. credit unions, building societies,
SME lenders, open-banking-reliant lenders, digital application-journey providers). The key design
point: `run_discovery_sweep()` builds `lead_source`/`orchestrator`/`repository` **once** and reuses
them across every query, calling the same `discover_and_qualify_leads()` per query. Because that
function already re-fetches `repository.all_domains()` fresh at the start of every call, a domain
saved by query 1 is automatically excluded from query 2's search — no extra cross-query bookkeeping
needed, it falls out of the existing per-call dedup for free. A query whose own `discover()` call
fails (e.g. a real OpenRouter "insufficient credits" error hit while building this feature) is
logged and skipped, exactly like a failing candidate within a single query — one bad query doesn't
cost you the queries that would have run after it.

### `alembic/` — migrations from day one
Even with a single table, migrations are set up now rather than hand-editing the production schema
later. `alembic/env.py` is wired to `app.db.models.Base` (so `--autogenerate` can diff against the
real models) and to `Settings.database_url` (so there's exactly one source of truth for the
connection string — no separate URL to keep in sync in `alembic.ini`).

---

## 4. Alembic migrations, explained (if you've never used them)

If you know Django, this is the same idea with different names. Both solve the same problem: your
database schema needs to change over time (add a table, add a column, rename something), and you
need a repeatable, ordered, version-controlled way to apply those changes — to your laptop, a
teammate's laptop, and production, all ending up identical.

| Concept | Django | Alembic (what we used) |
|---|---|---|
| Where models live | `models.py` (Django ORM classes) | `app/db/models.py` — `LeadRecord` (SQLAlchemy ORM class) |
| Generate a migration from model changes | `python manage.py makemigrations` | `alembic revision --autogenerate -m "..."` |
| The generated file | `myapp/migrations/0001_initial.py` | `alembic/versions/ae131006cca1_create_leads_table.py` |
| Apply migrations to the DB | `python manage.py migrate` | `alembic upgrade head` |
| Track "which migrations have run" | Django's own `django_migrations` table | Alembic's own `alembic_version` table |
| Undo a migration | `python manage.py migrate myapp 0001` (go back) | `alembic downgrade -1` |

Same mental model: autogenerate diffs your models against the live DB and writes the migration file;
migrate/upgrade actually runs it.

### What literally happened in this project, step by step

1. **The model was defined** — `app/db/models.py` — `class LeadRecord(Base): __tablename__ =
   "leads"`, with columns like `domain`, `company_name`, `score`, etc. At this point nothing in the
   database exists yet — it's just a Python class.

2. **`alembic init alembic`** — one-time setup. Created the `alembic/` folder and `alembic.ini`.
   This is the Alembic-equivalent of Django's migration machinery already existing inside
   `manage.py` — you only do this once per project.

3. **`alembic/env.py` was wired to the actual models and settings**:
   ```python
   from app.config import get_settings
   from app.db.models import Base

   config.set_main_option("sqlalchemy.url", get_settings().database_url)
   target_metadata = Base.metadata
   ```
   This tells Alembic two things: *where's the database* (`DATABASE_URL` from `.env`) and *what the
   models look like* (`Base.metadata`, which knows about `LeadRecord` since it inherits from
   `Base`).

4. **`alembic revision --autogenerate -m "create leads table"`** — the "makemigrations" step.
   Alembic connected to the (empty) `leadgen` database, saw it had no `leads` table, compared that
   to what `LeadRecord` says should exist, and wrote the diff as a migration file:
   `alembic/versions/ae131006cca1_create_leads_table.py`. It's plain Python calling
   `op.create_table("leads", ...)` with every column spelled out, plus a `downgrade()` that reverses
   it (`op.drop_table`).

5. **`alembic upgrade head`** — the "migrate" step. Alembic connected to `leadgen` and actually ran
   the `upgrade()` function from that file, executing the real `CREATE TABLE leads (...)` SQL. This
   is the moment the table was actually created — step 4 only wrote a plan; step 5 executed it.

### How future schema changes work

Say next month a `phone_number` column gets added to `LeadRecord`. The workflow repeats:

```bash
# 1. Add the column to LeadRecord in app/db/models.py first
# 2. Generate the migration (Alembic diffs old vs new model)
alembic revision --autogenerate -m "add phone_number to leads"
# 3. Review the generated file (always check autogenerate output -- it's not always perfect)
# 4. Apply it
alembic upgrade head
```

Alembic knows "add phone_number" comes *after* "create leads table" because each migration file has
a `down_revision` pointing to the previous one (`ae131006cca1_create_leads_table.py` has
`down_revision = None` since it's the first). It's a linked list of changes, same as Django's
migration dependency graph.

**One gotcha worth knowing:** autogenerate only detects *some* changes reliably (new tables/columns,
yes; some column-type changes or renames, not always — it can mistake a rename for a drop+add).
Always eyeball the generated file before running `upgrade`, same caution Django docs give you.

---

## 5. Key concepts (transferable)

| Concept | In one line | When to reach for it |
|---------|-------------|----------------------|
| Upsert by natural key | Look up by the real-world identifier (a domain), not a surrogate you invent | Any time "save" should mean "create or update," not "always insert" |
| Dependency-injected `session_factory` | Pass in how to get a session, don't construct one internally | Keeps SQLite-in-tests / Postgres-in-prod on the exact same code path |
| Permanent vs. time-windowed exclusion | Simplest rule that solves the actual problem, not the most flexible one | Add a time window only once someone actually needs "re-eligible after N days" |
| Migrations from day one | Set up the schema-evolution seam before the second migration is urgent | Any project expected to add columns/tables later — which is most of them |

---

## 6. How to run & test it

```bash
# Repository + dedup/persistence tests -- pure SQLite in-memory, no real Postgres needed
./.venv/Scripts/python.exe -m pytest tests/db tests/agents/test_discovery_pipeline.py -v

# Full suite
./.venv/Scripts/python.exe -m pytest -q
```

### What the tests prove
- `test_repository.py` — `save()` creates a new row, upserts (not duplicates) on a repeat domain
  while preserving `first_seen_at`, and silently skips leads with no domain; `filter_unseen()`
  excludes any domain that's ever been saved, regardless of how long ago (proving dedup really is
  permanent, not time-based).
- `test_discovery_pipeline.py` — dedup skips previously-seen domains only when
  `skip_seen_domains=True`; every processed lead gets persisted; `run_discovery_pipeline()` wires a
  real repository through from `settings`.

### Trying it for real (against your own local Postgres)
```bash
# One-time setup
./.venv/Scripts/python.exe -m alembic upgrade head

# Run twice with the same query
./.venv/Scripts/python.exe scripts/try_discovery.py "credit unions in the UK"
./.venv/Scripts/python.exe scripts/try_discovery.py "credit unions in the UK"
```
On the real run of this phase, the first call found and persisted 2 leads (`glasgowcu.com`,
`creditunion.co.uk`). A later call with the same query discovered 2 candidates again, but the log
reported `1 LEAD(S) FOUND` — one candidate was already in `leads` and was skipped before it ever
reached the orchestrator; only the genuinely new domain (`nhscreditunion.com`) was researched,
qualified, and saved. The `leads` table ended with exactly 3 rows, no duplicates.

---

## 7. What's next

Phase 8 — **API layer**: FastAPI endpoints exposing the discovery/lead pipeline as a real service,
reading from and writing to this same `leads` table.
