# Phase 10: n8n Integration — Design Spec

**Date:** 2026-07-22
**Status:** Approved design (pre-implementation)

---

## 1. Purpose & Context

Every phase so far has run through a CLI script or a direct API call, with a human (the user)
watching the terminal the whole time. That's fine for building and debugging, but it isn't how a
real lead-gen operation works: someone needs to review an outreach draft before it goes to a real
prospect, someone needs to be told when a run fails, and something needs to actually kick off a run
in the first place without a developer typing a command.

Phase 10 is the original Phase-1 design spec's §10 (n8n Orchestration), finally built: n8n
orchestrates the existing hand-built Python agent (Phases 3-9) through three workflows — it never
re-implements any agent logic, it only calls the API and handles the human-in-the-loop routing,
sending, and alerting around it. This is a deliberate showcase choice (confirmed during
brainstorming): n8n orchestrating a real custom multi-agent system, not a parallel agent built
inside n8n's own low-code AI Agent node.

**Explicit scope note, confirmed with the user:** this phase builds the Python/API side with full
TDD (as every prior phase has), and hand-authors the three n8n workflow JSON files as real,
importable artifacts in the repo — but the workflows themselves are **not live-tested or run** as
part of this phase. Credential entry (Slack bot tokens, Gmail OAuth) and live workflow execution
are the user's own manual step, to be done whenever they choose, not a completion requirement here.

## 2. Scope

### In scope (Phase 10)
- A new `leads.approval_status` column and the repository/API logic to read and transition it.
- Three new/extended API endpoints for n8n to call: list pending drafts, record an approve/reject
  decision, mark a lead as sent.
- A push-based alerting hook: the app calls an n8n webhook the instant a background job fails.
- Three hand-authored n8n workflow JSON files, checked into a new `n8n/` directory: trigger/
  ingestion, approval + send, alerting.
- An `n8n` service added to the existing `deploy/docker-compose.yml` (alongside the `app`/`db`
  services already there from Phase 1), so the user can bring it up locally whenever they're ready.

### Out of scope (deferred)
- Live-testing/running any n8n workflow, or configuring real Slack/Gmail credentials — explicitly
  deferred to the user's own time, not part of this phase's Definition of Done.
- A native n8n AI Agent (LangChain-based node built entirely in n8n's canvas) — considered during
  brainstorming and explicitly rejected in favor of orchestrating the existing Python agent.
- CRM sync and scheduled/Cron-triggered discovery sweeps — mentioned during brainstorming as other
  ways n8n could add value, but not part of this phase's three confirmed workflows.
- Kubernetes/minikube deployment and the Supabase Postgres migration — that's Phase 11 (Deploy),
  and Phase 11 has itself been simplified to docker-compose only (see §8).

## 3. Data Model: Lead Approval Status

### New column
`app/db/models.py`'s `LeadRecord` gains:
```python
approval_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
```
Values: `None` (disqualified lead, or a qualified lead that hasn't been through this phase's logic
yet — see backfill note below), `"pending"` (qualified lead with a draft, awaiting human review),
`"approved"`, `"rejected"`, `"sent"`.

### Migration
A new Alembic migration adds the nullable column with no backfill/default — existing rows (saved
before this phase) simply get `NULL`. There's no need to backfill them to `"pending"`: they were
already reviewed by a human implicitly (the user ran the CLI/API themselves in earlier phases), and
retroactively surfacing old leads into a new Slack approval queue would just create noise.

### `LeadRepository` changes
- **`save(lead)`**: after the existing field updates, if `lead.status == "qualified"` **and** the
  record's current `approval_status` is `None` (i.e. this is the first time this domain has been
  saved as qualified under this phase's logic), set `approval_status = "pending"`. If
  `approval_status` is already set to something else (`"approved"`/`"rejected"`/`"sent"`), leave it
  untouched — a later re-save (e.g. re-running Discovery on a domain that somehow bypassed
  permanent dedup) must never silently reset a human decision back to `"pending"`. Disqualified
  leads never get an `approval_status` set.
- **`list_leads(status=None, approval_status=None, limit=50, offset=0)`**: gains a new optional
  `approval_status` filter, applied the same way the existing `status` filter is (an additional
  `.where(...)` clause only when the parameter is given). This is the method n8n's approval
  workflow polls through the API (`GET /v1/leads?approval_status=pending`).
- **`get_by_domain`**, **`filter_unseen`**, **`all_domains`**: unchanged.

## 4. API Changes

All new/changed routes stay on the existing `app/api/leads.py` router (prefix `/v1`, already behind
`require_api_key` at the router level — n8n's HTTP Request nodes will carry the same `X-API-Key`
header, configured as an n8n credential, not committed anywhere).

- **`GET /v1/leads`** — gains an optional `approval_status` query parameter
  (`Literal["pending", "approved", "rejected", "sent"] | None`), passed straight through to
  `list_leads()`. This is the endpoint n8n's approval workflow polls for new work.
- **`POST /v1/leads/{domain}/approval`** — new endpoint. Body: `{"decision": "approved" | "rejected"}`.
  - 404 if the domain doesn't exist.
  - 400 if the lead's current `approval_status` isn't `"pending"` (nothing to decide — either it
    was never a qualified lead with a draft, or it's already been decided).
  - On success: sets `approval_status` to the given decision, returns the updated
    `LeadRecordOut`.
- **`POST /v1/leads/{domain}/sent`** — new endpoint, no body.
  - 404 if the domain doesn't exist.
  - 400 if the lead's current `approval_status` isn't `"approved"` (can't mark something as sent
    that was never approved, or is already sent).
  - On success: sets `approval_status = "sent"`, returns the updated `LeadRecordOut`.
- **`LeadRecordOut`** (the existing response model) gains an `approval_status: str | None` field.

Both new endpoints go through `build_lead_repository(settings)` exactly like the existing
`get_lead`/`list_leads` handlers — no new repository construction pattern, no bypassing the
existing `_get_engine()` caching from Phase 8.

## 5. Alerting: Push Webhook

### Config
`Settings` gains one new optional field:
```python
n8n_alert_webhook_url: str | None = None
```
Matches this project's established "no-op when unset" pattern (API keys, Langfuse config): if
unset, alerting is silently disabled — no code path touches the network.

### `app/observability/alerting.py` (new file)
```python
def send_alert(settings: Settings, *, kind: str, status: str, error: str | None) -> None:
```
- Returns immediately (no-op) if `settings.n8n_alert_webhook_url` is falsy.
- Otherwise, POSTs a small JSON payload (`{"kind": kind, "status": status, "error": error}`) to the
  configured webhook URL via `httpx` (already a project dependency since Phase 3 — no new package
  needed).
- Wrapped in a broad `try/except Exception`, logging a warning on failure but never raising — a
  webhook being unreachable, misconfigured, or slow must never break the actual job-tracking logic
  it's reporting on. This mirrors the same reasoning Phase 9's `traced_span` no-op design already
  established: an observability/notification side-channel must be strictly additive, never a new
  failure mode for the thing it's observing.

### Wiring
`app/api/jobs.py`'s `JobStore.mark_failed()` gains a call to `send_alert(settings, kind=job.kind,
status="failed", error=error)`, alongside the existing `record_job_outcome()` call from Phase 9.
`mark_done()` is untouched — this phase only alerts on failure, matching the original spec's "on
error-rate / health failure -> notify" framing. Since `JobStore` doesn't currently hold a `Settings`
reference (it's a plain in-memory store, deliberately decoupled from config), `mark_failed` gains
an optional `settings: Settings | None = None` parameter; the one real call site
(`app/api/leads.py`'s `_run_lead_job`/`_run_discovery_job` exception handlers) passes the `settings`
it already has in scope. When `settings` is `None` (as in every pre-existing test that doesn't pass
it), alerting is skipped entirely — backward compatible with every existing `mark_failed` call/test.

## 6. n8n Workflows (hand-authored JSON, `n8n/` directory)

n8n workflows are just JSON: a list of nodes (type, parameters, position) and a connections map.
These are hand-written directly (not built through n8n's UI), matching how n8n itself exports them,
so they're valid to import via n8n's "Import from File" feature once the user is ready.

- **`n8n/01-trigger-ingestion.json`** — `Webhook` node (path e.g. `/trigger-lead`) accepting a JSON
  body (`{"target": "..."}` or `{"query": "..."}`) → `HTTP Request` node calling `POST /v1/leads` or
  `POST /v1/discovery` on the app (`X-API-Key` header sourced from an n8n credential, not hardcoded
  in the JSON) → `Respond to Webhook` node returning the app's `{"job_id": ..., "status": ...}`
  straight back to whoever called the n8n webhook.
- **`n8n/02-approval-and-send.json`** — two triggers feeding one workflow:
  - A `Schedule Trigger` (Cron, e.g. every 5 minutes) → `HTTP Request` (`GET
    /v1/leads?approval_status=pending`) → `Slack` node posting each pending lead (company name,
    qualification score/reasoning, draft subject/body) to a configured channel with interactive
    Approve/Reject buttons.
  - A `Webhook` node receiving Slack's button-click callback → `HTTP Request`
    (`POST /v1/leads/{domain}/approval`, decision from which button was clicked) → an `IF` node
    branching on the decision → (approved branch) `HTTP Request` (`GET /v1/leads/{domain}` to fetch
    the contact email + draft subject/body) → `Gmail` node sending the email → `HTTP Request`
    (`POST /v1/leads/{domain}/sent`).
- **`n8n/03-alerting.json`** — `Webhook` node (receives this phase's `send_alert()` POST) → `Slack`
  node posting the failure (`kind`, `status`, `error`) to an ops channel.

Each file includes a top-of-file JSON comment-equivalent (n8n supports a workflow-level `notes`
field) briefly describing what it does and which credentials it expects the user to configure,
since there's no README rendering inside n8n's own UI.

## 7. docker-compose Changes

`deploy/docker-compose.yml` (already exists from Phase 1: `db` + `app` services) gains one more
service:
```yaml
n8n:
  image: n8nio/n8n
  ports:
    - "5678:5678"
  volumes:
    - n8n_data:/home/node/.n8n
```
n8n's default SQLite-backed storage (its own volume, not the project's Postgres) is simplest for
local dev and keeps n8n's internal workflow/credential storage fully decoupled from the app's
`leads` database. Slack bot tokens, Gmail OAuth, and the app's `X-API-Key` value are configured by
the user inside n8n's UI at runtime (Credentials store) — never written to `.env`, `docker-compose.yml`,
or committed anywhere.

## 8. New Dependencies

None. `httpx` (used by `alerting.py`) is already a project dependency since Phase 3.

## 9. Testing

- **Python/API side — full TDD**, same discipline as every prior phase:
  - Migration + `LeadRecord.approval_status` column.
  - `LeadRepository.save()`'s pending-on-first-save-only logic, and `list_leads()`'s new filter.
  - Both new endpoints (`POST /v1/leads/{domain}/approval`, `POST /v1/leads/{domain}/sent`) —
    success paths and every 400/404 error path.
  - `alerting.py::send_alert()` — no-op when unset, POSTs correctly when configured, never raises
    even when the webhook call fails (mock `httpx` to simulate a network error).
  - `JobStore.mark_failed()`'s new optional `settings` parameter — confirms `send_alert` is called
    with the right arguments when `settings` is passed, and that omitting it (every pre-existing
    test) still passes unchanged.
- **n8n workflow JSON files** — validated only for well-formed JSON structure (each file parses,
  has the expected node types present). Per the scope note in §1, they are not imported into a
  running n8n instance or executed as part of this phase.

## 10. What's Next

Phase 11 — **Deploy**, simplified (per explicit user direction) to docker-compose only: setup
scripts and deployment documentation added to the repo, the existing `deploy/docker-compose.yml`
(now including `db`, `app`, and `n8n`) becomes the complete, runnable local/demo deployment.
Kubernetes/minikube and the Supabase managed-Postgres migration are documented as a future path,
not built. After Phase 11, the project concludes with a single comprehensive documentation page
(published as a Claude Artifact, per the user's explicit direction) covering architecture, data
flow, the full API reference, the DB schema, and the phase-by-phase build story — replacing the
originally-planned Phase 12 (Quality + polish) entirely.
