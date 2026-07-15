# Phase 8: API Layer — Design Spec

**Date:** 2026-07-15
**Status:** Approved design (pre-implementation)

---

## 1. Purpose & Context

Phases 1–7 built the whole pipeline as a set of importable Python functions, exercised only via
CLI scripts (`scripts/try_lead.py`, `scripts/try_discovery.py`). Phase 8 exposes that same
pipeline over HTTP, so it can be triggered and queried by something other than a terminal — most
immediately, Phase 9's React dashboard.

The original Phase-1 design spec (`docs/superpowers/specs/2026-07-07-lead-generation-agent-design.md`,
§6) sketched an API surface before Discovery, multi-query sweep, or Postgres persistence existed.
This spec supersedes that sketch for what actually gets built in Phase 8, scoped down to what's
needed now.

## 2. Scope

### In scope (Phase 8)
- Trigger a single-lead run (`POST /v1/leads`).
- Trigger a discovery run — single query, multiple queries, or the configured default
  (`POST /v1/discovery`).
- Poll a triggered run's status/result (`GET /v1/jobs/{job_id}`).
- List and fetch already-persisted leads from Postgres (`GET /v1/leads`, `GET /v1/leads/{domain}`).
- Header-based API-key auth on every `/v1/*` route, no-op when `API_KEY` is unset.

### Out of scope (deferred to later phases)
- `POST /v1/leads/{id}/export` — Excel export already happens automatically via the CLI scripts;
  not needed via API yet.
- `POST /v1/ingest` (bulk targets) — explicitly reserved for the Phase 11 n8n integration.
- Rate limiting — noted in `Settings.rate_limit_per_min` (unused since Phase 1) but not enforced
  this phase.
- `GET /metrics` (Prometheus) — Phase 9/10's job (Observability).
- A durable `jobs` table — an in-memory `JobStore` is sufficient for now (see §4).

## 3. Architecture

```
POST /v1/leads {target}       ──┐
POST /v1/discovery {query(s)} ──┼──► JobStore.create() ──► 202 {job_id, status: "queued"}
                                 │         │
                                 │         ▼ (FastAPI BackgroundTasks, runs after response is sent)
                                 │    orchestrator.run() / run_discovery_sweep()
                                 │         │
                                 │         ▼
                                 │    JobStore.mark_done(job_id, result) / mark_failed(job_id, error)
                                 │
GET /v1/jobs/{job_id}  ─────────┘──► {status, result: Lead | list[Lead] | None, error}

GET /v1/leads          ──► LeadRepository.list_leads() ──► persisted rows (Postgres)
GET /v1/leads/{domain} ──► LeadRepository.get_by_domain()
```

**Why background jobs, not synchronous responses:** a discovery sweep across several queries, each
researching several candidates via multiple LLM calls, can take many minutes (confirmed during
Phase 7 testing — a 5-query sweep ran long enough to hit a real rate/credit limit mid-run). A
synchronous HTTP request risks client/proxy timeouts with no way to see progress. Returning a
`job_id` immediately and letting the caller poll avoids that entirely, and gives Phase 9's
dashboard something to show progress against.

**Why an in-memory `JobStore`, not a Postgres table:** job status is ephemeral, in-process state —
the durable output of a job (the `Lead`(s) it produced) is already saved to the `leads` table by
the existing pipeline the moment it completes. Losing job history on a restart is an acceptable
trade-off now; if job history needs to survive restarts later, that's Phase 10 (Observability)
territory, and risks being built twice if attempted now alongside the already-planned `agent_runs`
table.

## 4. New Components

### `app/api/jobs.py` — `JobStore`
- `Job` (Pydantic model): `job_id: str`, `kind: Literal["lead", "discovery"]`,
  `status: Literal["queued", "running", "done", "failed"]`, `created_at: datetime`,
  `finished_at: datetime | None`, `result: Any = None` (a `Lead` for `kind="lead"`,
  a `list[Lead]` for `kind="discovery"`), `error: str | None`.
- `JobStore`: `.create(kind) -> Job`, `.mark_running(job_id)`, `.mark_done(job_id, result)`,
  `.mark_failed(job_id, error)`, `.get(job_id) -> Job | None`. Backed by a plain `dict[str, Job]`.
- `get_job_store()` factory returning a module-level singleton (mirrors `get_settings()`'s
  `lru_cache` pattern) — overridable per-test via `app.dependency_overrides`.

### `app/api/auth.py` — `require_api_key`
A FastAPI dependency: reads `X-API-Key` header, compares to `settings.api_key`. If
`settings.api_key` is unset/empty, the check is skipped entirely (zero-friction local dev, matches
this project's existing "no key configured → skip" pattern used for LLM provider keys). Applied at
the router level (`dependencies=[Depends(require_api_key)]`) so every `/v1/*` route is covered
without repeating it per-endpoint. `/health` and `/ready` stay on their own unauthenticated router,
unchanged — Kubernetes liveness/readiness probes can't send custom headers.

### `app/api/leads.py` — the router
Two `POST` endpoints (trigger a job, return 202 + `job_id` immediately), three `GET` endpoints
(poll a job; list persisted leads; fetch one persisted lead). Route handlers are thin — they
resolve `Settings`/`JobStore` via `Depends`, create a job, and hand off to a background function
that calls the *existing* pipeline entry points (`build_lead_orchestrator_agent`,
`run_discovery_sweep`, `build_lead_repository`) exactly as the CLI scripts already do. No pipeline
logic is duplicated in the API layer.

`POST /v1/discovery` request body accepts `query: str | None`, `queries: list[str] | None`,
`max_results: int | None`. Resolution order: `queries` if given, else `[query]` if given, else
`parse_discovery_queries(settings.discovery_queries)` if non-empty, else a single default query
(`"credit unions in the UK"`, matching the CLI script's existing fallback).

### `app/db/repository.py` — two new read methods on `LeadRepository`
- `list_leads(status: str | None = None, limit: int = 50, offset: int = 0) -> list[LeadRecord]` —
  ordered by `last_seen_at` descending (most recently touched first), optionally filtered by
  `status`.
- `get_by_domain(domain: str) -> LeadRecord | None`.

Both return the SQLAlchemy `LeadRecord` model directly; API responses use a `LeadRecordOut`
Pydantic model (`model_config = ConfigDict(from_attributes=True)`) to serialize it, since
`LeadRecord` has no relationships/deferred attributes, this is safe even after the session closes.

### `app/main.py`
Registers the new router alongside the existing health router: `app.include_router(leads_router)`.

## 5. Error Handling

- `GET /v1/jobs/{job_id}` for an unknown `job_id` → `404`.
- `GET /v1/leads/{domain}` for a domain not in the table → `404`.
- A background job whose pipeline call raises is caught by the background function itself,
  recorded via `JobStore.mark_failed(job_id, str(exc))` — the job transitions to `"failed"` with
  the error message visible via `GET /v1/jobs/{job_id}`, rather than crashing silently (consistent
  with the per-candidate/per-query resilience already built into the pipeline in Phase 7).
- Missing/invalid `X-API-Key` (when `API_KEY` is configured) → `401`.

## 6. Testing

FastAPI's `TestClient` executes `BackgroundTasks` synchronously as part of the request/response
cycle, so a test can `POST` a job and immediately `GET /v1/jobs/{job_id}` and see `"done"` — no
polling or sleeping needed in tests. Collaborators are monkeypatched at the same module level the
existing pipeline tests already use (`build_lead_orchestrator_agent`, `run_discovery_sweep`,
`build_lead_repository`), so no new mocking pattern is introduced. `Settings` and `JobStore` are
swapped per-test via `app.dependency_overrides` for isolation between tests.

Coverage: job creation + polling for both endpoint kinds (success and failure paths), auth
(no key configured → open; key configured + missing/wrong header → 401; correct header → 200),
`list_leads`/`get_by_domain` repository methods (reusing the existing in-memory-SQLite test
pattern from Phase 7), and the leads list/fetch endpoints against a seeded repository.

## 7. What's Next

Phase 9 — Dashboard (React + Vite): a minimal UI that calls this API to trigger runs, poll job
status, and display persisted leads.
