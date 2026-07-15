# Phase 8 — API Layer (Learning Guide)

> **Goal of this phase:** stop being a set of CLI scripts. Turn `run_discovery_pipeline()` /
> `LeadOrchestratorAgent.run()` into a real HTTP service that Phase 9's dashboard (or anything
> else) can trigger and poll.

---

## 1. What & why

Every phase before this one was exercised through `scripts/try_lead.py` and
`scripts/try_discovery.py` — genuinely useful, but only usable from a terminal on the same machine
as the code. Phase 8 exposes the same pipeline over HTTP, without rewriting any of it.

**Why background jobs, not synchronous responses.** A discovery sweep across several queries, each
researching several candidates through multiple LLM calls, can take many minutes — this isn't
theoretical: a 5-query sweep during Phase 7 testing ran long enough to hit a real OpenRouter
rate/credit limit mid-run. Holding an HTTP connection open for that long risks client and proxy
timeouts, with no way for the caller to see progress in the meantime. Returning a `job_id`
immediately and letting the caller poll `GET /v1/jobs/{job_id}` sidesteps that entirely, and gives
Phase 9's dashboard something concrete to show progress against.

**Why an in-memory `JobStore`, not a Postgres table.** Job status (`queued` → `running` →
`done`/`failed`) is ephemeral, in-process bookkeeping — it only matters while someone might be
polling it. The thing worth keeping forever, a completed job's `Lead`(s), is already written to the
`leads` table by the existing pipeline the moment the job finishes (`app/api/leads.py:31`,
`_run_lead_job` calls `build_lead_repository(settings).save(lead)` before marking the job done).
Losing in-flight job history on a process restart is an acceptable trade-off now — the durable
output survives that restart just fine. A `jobs` table would only earn its keep once job history
itself needs to survive restarts, which is Phase 10 (Observability) territory; building it now
would risk duplicating the already-planned `agent_runs` table.

**Why auth is a no-op when `API_KEY` is unset.** This matches a pattern the project already has:
Phase 2's LLM provider keys work the same way (unset → that provider just isn't available, nothing
crashes). `require_api_key` (`app/api/auth.py`) skips the check entirely when
`settings.api_key` is falsy, so local development and the test suite need zero configuration to
exercise every route, while a deployed instance can lock itself down with one env var.

---

## 2. The flow

```
  POST /v1/leads {target}          ─┐
  POST /v1/discovery {query(s)}    ─┼──► JobStore.create(kind) ──► 202 {job_id, status: "queued"}
                                     │            │
                                     │            ▼ (FastAPI BackgroundTasks -- runs *after* the
                                     │               202 response has already been sent)
                                     │       job_store.mark_running(job_id)
                                     │       orchestrator.run(target) / run_discovery_sweep(...)
                                     │       -- the exact same functions scripts/try_lead.py and
                                     │          scripts/try_discovery.py already call
                                     │            │
                                     │       success ──► repository.save(lead) (lead only)
                                     │                    job_store.mark_done(job_id, result)
                                     │       failure ──► job_store.mark_failed(job_id, str(exc))
                                     │
  GET /v1/jobs/{job_id}  ───────────┘──► {status, result: Lead | list[Lead] | null, error}

  GET /v1/leads          ──► LeadRepository.list_leads()   ──► rows from the Phase 7 `leads` table
  GET /v1/leads/{domain} ──► LeadRepository.get_by_domain()
```

Every `/v1/*` route sits behind `require_api_key` (wired once at the router level, not per-route).
`/health` and `/ready` (from Phase 1) are on their own router and stay unauthenticated — Kubernetes
liveness/readiness probes can't send a custom header.

---

## 3. File-by-file walkthrough

### `app/api/jobs.py` — `Job` and `JobStore`
- **`Job`** is a Pydantic model, not a plain dict: `job_id`, `kind: Literal["lead", "discovery"]`,
  `status: Literal["queued", "running", "done", "failed"]`, `created_at`, `finished_at | None`,
  `result: Any = None`, `error: str | None = None`. Using Pydantic here means it doubles as the
  FastAPI `response_model` for `GET /v1/jobs/{job_id}` for free — no separate serialization type
  needed.
- **`JobStore`** is deliberately dumb: a `dict[str, Job]` plus four mutator methods
  (`create`, `mark_running`, `mark_done`, `mark_failed`) and one reader (`get`). No locking, no
  expiry, no persistence — it doesn't need any of that to do its one job (let a background task
  update status, let a poller read it).
- **Why `get_job_store()` is a singleton via `@lru_cache`, not a fresh `JobStore()` per request.**
  A `Job` created by one request (`POST /v1/leads`) has to still be visible to a *later, separate*
  request (`GET /v1/jobs/{job_id}`) — if FastAPI's `Depends(get_job_store)` constructed a new
  `JobStore` every time, every job would vanish the instant the handler returned. `@lru_cache`
  (the exact same pattern `get_settings()` already uses in `app/config.py`) makes every call to
  `get_job_store()` within the process return the *same* instance. Tests still get isolation: each
  test constructs its own `JobStore()` and overrides the dependency
  (`app.dependency_overrides[get_job_store] = lambda: job_store`, see `tests/api/test_leads.py:40-44`)
  rather than sharing the real process-wide singleton across tests.

### `app/api/auth.py` — `require_api_key`
A ten-line FastAPI dependency: read the `X-API-Key` header, compare it to `settings.api_key`. If
`settings.api_key` is unset, return immediately — no comparison happens at all. If it's set and the
header doesn't match (missing or wrong), raise `HTTPException(401)`.

**Why it's wired at the router level, not repeated per-route.** `app/api/leads.py:14` declares
`router = APIRouter(prefix="/v1", tags=["leads"], dependencies=[Depends(require_api_key)])` once,
and every route registered on that router inherits it automatically. Repeating
`dependencies=[Depends(require_api_key)]` on each of the five `/v1/*` endpoints individually would
work identically today, but it's one more place someone adding a sixth endpoint could forget —
attaching it to the router closes that gap structurally instead of relying on everyone remembering.

### `app/api/leads.py` — the router
Five endpoints, and the recurring theme is: **the route handlers don't contain pipeline logic, they
call the pipeline.**

- **`POST /v1/leads`** — `trigger_lead_run` creates a `Job` via `job_store.create(kind="lead")`,
  schedules `_run_lead_job` as a `BackgroundTasks` callback, and returns `202 {job_id, status}`
  immediately. `_run_lead_job` itself calls `build_lead_orchestrator_agent(settings)` and
  `orchestrator.run(target)` — the exact same two calls `scripts/try_lead.py` makes — then
  `build_lead_repository(settings).save(lead)` before marking the job done. If anything in that
  `try` block raises, the `except Exception` catches it and records `job_store.mark_failed(job_id,
  str(exc))` rather than crashing the background task silently.
- **`GET /v1/jobs/{job_id}`** — looks the job up; `404` if `job_store.get(job_id)` returns `None`.
- **`POST /v1/discovery`** — same shape, calling `run_discovery_sweep(settings, queries=...,
  max_results=...)` (the function `scripts/try_discovery.py` already uses for multi-query sweeps)
  in the background job. The interesting part is **query resolution order**, in
  `_resolve_discovery_queries`: explicit `body.queries` list wins first, then a single `body.query`
  string, then whatever `DISCOVERY_QUERIES` is configured in settings (parsed via the existing
  `parse_discovery_queries`), and only if none of those produced anything, a hardcoded default
  (`"credit unions in the UK"`) — matching the CLI script's own existing fallback, so hitting the
  endpoint with an empty body behaves exactly like running `scripts/try_discovery.py` with no
  arguments.
- **`GET /v1/leads`** and **`GET /v1/leads/{domain}`** — thin wrappers over the two new
  `LeadRepository` read methods (see below), converting the SQLAlchemy `LeadRecord` into a
  `LeadRecordOut` Pydantic model for the response. `GET /v1/leads/{domain}` 404s when
  `get_by_domain` returns `None`.

Nothing in this file researches a company, calls an LLM, or touches SQL directly — it all defers to
functions that already existed before Phase 8. That's a deliberate constraint, not an accident: if
the API layer duplicated any of that logic, the CLI scripts and the API could quietly drift apart
in behavior over time.

### `app/db/repository.py` — two new read methods
- **`list_leads(status=None, limit=50, offset=0)`** — `select(LeadRecord).order_by(
  LeadRecord.last_seen_at.desc())`, optionally filtered by `status`, then `.offset(offset).limit(
  limit)`. Ordering by `last_seen_at` descending means the most recently (re-)touched lead shows up
  first — the same column Phase 7's upsert-by-domain logic already maintains, reused here for free.
- **`get_by_domain(domain)`** — a single `select(...).where(LeadRecord.domain == domain)`,
  returning `None` if nothing matches.

Both return the SQLAlchemy `LeadRecord` ORM object directly, not a hand-mapped dict — `leads.py`'s
`LeadRecordOut(model_config = ConfigDict(from_attributes=True))` reads attributes straight off it.
This is safe here specifically because `LeadRecord` has no relationships or deferred/lazy-loaded
columns that would need an open session to resolve after the `with self._session_factory() as
session:` block in `list_leads`/`get_by_domain` has already closed.

### `app/main.py`
One line added to the existing app factory: `app.include_router(leads_router)`, alongside the
Phase 1 `health_router`. No other wiring needed — the router already carries its own prefix
(`/v1`), tags, and auth dependency.

---

## 4. Key concepts (transferable)

| Concept | In one line | When to reach for it |
|---------|-------------|----------------------|
| Background-job-plus-polling | Return an ID immediately, do the slow work after the response is sent, let the caller poll for the result | Any operation whose duration is unpredictable or can exceed a client/proxy timeout |
| Ephemeral vs. durable state | In-memory state for "is it done yet," a real table only for what must outlive the process | Job/task status that's only useful while someone might be watching it |
| No-op-when-unset auth | Skip the check entirely if the secret isn't configured, rather than requiring a dummy value | Local dev and CI should need zero setup; production opts in by setting the value |
| `lru_cache` for process-wide singletons | One cached instance shared by every `Depends()` call in the process, overridable per-test | Any dependency that must persist state across otherwise-independent requests |
| Testing `BackgroundTasks` synchronously | `TestClient` runs the background task inline before returning the response, so `POST` then immediately `GET` sees the finished state | Testing async-looking flows without real sleeping, threading, or polling loops |
| `app.dependency_overrides` for isolation | Swap `Settings`/`JobStore`/anything else `Depends()`-injected per test, no monkeypatching FastAPI internals | Any FastAPI app under test that uses `Depends()` for its collaborators |

---

## 5. How to run & test it

```bash
# API + repository read-method tests -- no network, no real Postgres (SQLite in-memory / TestClient)
./.venv/Scripts/python.exe -m pytest tests/api tests/db -v

# Full suite
./.venv/Scripts/python.exe -m pytest -q
```

### What the tests prove
- `test_jobs.py` — `JobStore.create()` starts a job as `"queued"` with no result/error;
  `mark_running`/`mark_done`/`mark_failed` transition status correctly and set `finished_at`;
  `get()` returns `None` for an unknown `job_id`.
- `test_auth.py` — `require_api_key` is a pure no-op when `settings.api_key` is unset; raises
  `HTTPException(401)` for a missing or wrong header when a key *is* configured; passes silently
  when the header matches.
- `test_leads.py` — the full request/response cycle through `TestClient`, with
  `build_lead_orchestrator_agent`/`build_lead_repository`/`run_discovery_sweep` monkeypatched at the
  module level (the same pattern Phase 5/7 pipeline tests already use): `POST /v1/leads` returns
  `202` with a `queued` job; a subsequent `GET /v1/jobs/{job_id}` shows `"done"` with the real
  `Lead` payload (proving `TestClient` really does run `BackgroundTasks` synchronously — no sleep or
  poll loop needed in the test); an orchestrator that raises leaves the job `"failed"` with the
  exception message visible; `GET /v1/jobs/{job_id}` 404s for an unknown ID; auth is exercised end
  to end (open when unset, `401` when wrong/missing, `200`/`202` when correct); all three discovery
  query-resolution branches (`queries` list, single `query`, settings fallback, hardcoded default)
  are each asserted directly; `GET /v1/leads` and `GET /v1/leads/{domain}` are tested against a
  fake repository seeded with `LeadRecord` rows, including the 404 case for an unknown domain.
- `test_repository.py` (`tests/db`) — the two new read methods: `list_leads()` defaults to most
  recent first, filters by `status`, and respects `limit`/`offset`; `get_by_domain()` returns the
  matching row or `None`.

### Trying it for real

```bash
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` — every `/v1/*` route Phase 8 added shows up there automatically
(FastAPI's generated OpenAPI docs), alongside the Phase 1 `/health`/`/ready` routes.

A real round-trip (with `API_KEY` unset, so no header needed):

```bash
# Trigger a single-lead run
curl -s -X POST http://localhost:8000/v1/leads \
  -H "Content-Type: application/json" \
  -d '{"target": "acme.com"}'
# => {"job_id": "…", "status": "queued"}

# Poll it (repeat until status is "done" or "failed")
curl -s http://localhost:8000/v1/jobs/<job_id>

# Once persisted, read it back from the leads table
curl -s http://localhost:8000/v1/leads/acme.com
```

With `API_KEY=secret123` set in `.env`, the same requests need `-H "X-API-Key: secret123"` or they
get a `401`.

---

## 6. What's next

Phase 9 — **Dashboard (React + Vite)**: a minimal UI that calls this API to trigger lead/discovery
runs, poll job status, and display persisted leads — the first non-CLI, non-`curl` consumer of
everything built so far.
