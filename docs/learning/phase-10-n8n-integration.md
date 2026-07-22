# Phase 10 — n8n Integration (Learning Guide)

> **Goal of this phase:** move the "should we send this?" decision out of code and into a human's
> Slack channel — without touching the research → qualify → draft pipeline itself. n8n becomes the
> orchestration layer that triggers runs, gates outreach behind approval, and pushes failure alerts;
> the Python app stays the thing that actually does the work.

---

## 1. What & why

**Why n8n orchestrates the existing Python agent rather than rebuilding it as a native n8n AI Agent
node.** n8n ships its own AI Agent node, and it would be technically possible to re-implement
research/qualify/draft as a chain of n8n nodes instead. That was an explicit choice made during
brainstorming for this phase: the point of Phase 10 is to demonstrate *operationalizing* a real,
already-working multi-agent system — wiring a workflow tool around it for triggering, human
approval, and alerting — not to throw away nine phases of a custom Python agent and re-derive a
weaker version of it in a low-code canvas. n8n calls the app over HTTP; it never reimplements what
the app already does well.

**Why `approval_status` is a column on `leads`, not a separate table.** A lead has exactly one
current approval state at a time (`pending` / `approved` / `rejected` / `sent`) — it's a state
machine, not a one-to-many relationship. A separate `lead_approvals` table would need a join on
every single read (`GET /v1/leads`, `GET /v1/leads/{domain}`) just to answer "is this lead
approved?", and would raise its own consistency question (what happens if a lead has two rows?).
One nullable `String(20)` column on the row that already represents the lead keeps one source of
truth and needs no join for what is fundamentally a simple status flag.

**Why the pending-on-first-save-only rule exists.** `LeadRepository.save()` is an upsert — the same
domain gets re-saved every time the orchestrator re-researches it (a re-run, a refreshed draft,
etc.). If every `save()` reset `approval_status` back to `"pending"` whenever the lead is
`"qualified"`, a human's already-recorded decision (`"approved"`, `"rejected"`, even `"sent"`) would
silently revert to pending the next time the pipeline happened to touch that domain again — a real
correctness bug, not a cosmetic one, since it could cause a rejected lead's outreach to get
re-queued for sending after a human explicitly said no. `save()` only sets `approval_status =
"pending"` when the column is currently `None` (a genuinely new qualified lead) — see
`app/db/repository.py:40-41`. Once any value is present, `save()` never touches it again; only
`set_approval_status()` can move it.

**Why alerting is push (the app calls n8n) rather than pull (n8n polling metrics).** Phase 9 already
exposes `job_outcomes_total{status="failed"}` on `/metrics`. n8n *could* poll that endpoint on a
schedule and diff the counter between polls to detect new failures, but that means n8n has to track
"what was the counter last time I checked" and compute a delta — extra state, extra logic, and a
detection delay of up to one poll interval. A push model is simpler and immediate: the moment
`JobStore.mark_failed()` runs, the app itself calls `send_alert()`, which POSTs a small JSON payload
straight to an n8n webhook. No counter-delta logic in n8n, no polling interval to tune, and the
alert fires the instant the failure happens rather than on the next scheduled check.

**Scope boundary — read this before assuming any of this runs today.** The three n8n workflow files
under `n8n/` are hand-authored, importable JSON (Task 7) and the `docker-compose.yml` `n8n` service
(Task 8) brings up an empty n8n instance — but nothing in this phase imported those workflows into a
running n8n, configured a Slack or Gmail credential, or fired a real webhook end to end. That was a
deliberate scope decision, not an oversight: building the workflows and the plumbing is this phase's
job; live-testing them (credentials, real Slack channel, real Gmail account) is manual, human work
the user does on their own schedule. Anywhere this guide says a workflow "does X," read that as "is
written to do X once imported and configured," not "has been observed doing X."

**Honest note on test coverage.** Tasks 1-4 (the `approval_status` column, the migration, the
repository logic, and the two new API endpoints) were built with full TDD — every behavior described
below for those files has a passing test backing it (`tests/db/test_models.py`,
`tests/db/test_repository.py`, `tests/api/test_leads.py`). Partway through this phase, the user
asked to stop writing new automated tests for the remaining tasks. Tasks 5-8 — `send_alert()`, its
wiring into `JobStore.mark_failed()`, the three n8n JSON files, and the `docker-compose.yml` change
— do **not** have new automated tests. They were verified by direct inspection (reading the code,
reading the JSON) and by re-running the full existing suite (186 tests passing) to confirm nothing
already covered broke. That's a real trade-off worth naming plainly: the alerting code path and the
n8n workflows are correct by inspection, not by test, and nobody has clicked "run" on them yet.

---

## 2. The flow

```
  (1) Trigger — n8n workflow 01, "Trigger Ingestion"
  ─────────────────────────────────────────────────
    External event / manual click
          │
          ▼
    Webhook - Trigger  (POST body: {"target": "acme.com"} or {"query"/"queries": [...]})
          │
          ▼
    HTTP Request - Call App
      routes to POST http://app:8000/v1/discovery   when body has query/queries
      routes to POST http://app:8000/v1/leads       otherwise
          │
          ▼
    Respond to Webhook  (echoes the app's 202 JobAccepted response back to the caller)


  (2) Approval + send — n8n workflow 02, "Approval and Send" (two independent chains)
  ─────────────────────────────────────────────────────────────────────────────────
    Chain A (poll + notify):
      Schedule Trigger (every 5 min)
            │
            ▼
      GET /v1/leads?approval_status=pending
            │
            ▼
      Slack - Post Draft With Approve/Reject buttons  (#lead-approvals)

    Chain B (human decides -> send):
      Slack button click -> Webhook - Slack Button Callback  ({domain, decision})
            │
            ▼
      POST /v1/leads/{domain}/approval   {"decision": "approved" | "rejected"}
            │
            ▼
      IF approval_status == "approved" ──── no ───▶ (stop; rejected leads go no further)
            │ yes
            ▼
      GET /v1/leads/{domain}          -- fetch the full lead (subject/body/contacts)
            │
            ▼
      Gmail - Send Approved Outreach
            │
            ▼
      POST /v1/leads/{domain}/sent


  (3) Alerting — n8n workflow 03, "Alerting" (push, not pull)
  ────────────────────────────────────────────────────────────
    app/api/jobs.py: JobStore.mark_failed()
            │
            ▼
    app/observability/alerting.py: send_alert()   -- POSTs {kind, status, error}, never raises
            │
            ▼
    Webhook - Receive Alert  (n8n)
            │
            ▼
    Slack - Post Alert  (#ops-alerts)
```

The three workflows are independent of each other — nothing in workflow 02 depends on workflow 01
having run moments earlier, and workflow 03 fires regardless of which endpoint originally kicked off
the failing job. They share only the app's HTTP API and the `approval_status` column as their common
ground truth.

---

## 3. File-by-file walkthrough

### `app/db/models.py` / `alembic/versions/b4f9c2a1d8e3_add_approval_status_to_leads.py`
- `LeadRecord` gains one field: `approval_status: Mapped[str | None] = mapped_column(String(20),
  nullable=True)` (`app/db/models.py:27`) — nullable because most rows (disqualified leads) never
  enter the approval flow at all and should just stay `None` forever, not get forced into some
  placeholder state.
- The migration (`b4f9c2a1d8e3`, chained after `ae131006cca1`) is a single additive
  `op.add_column(...)` with a matching `op.drop_column(...)` in `downgrade()` — no backfill needed
  since the column is nullable and every existing row simply gets `NULL`.

### `app/db/repository.py`
- **`save()`** (lines 17-43) is the upsert from Phase 7, with one new line:
  `if lead.status == "qualified" and record.approval_status is None: record.approval_status =
  "pending"` (line 40-41). This is the pending-on-first-save-only rule from section 1 — it only
  fires when the column is currently unset, so a lead that already has a decision recorded keeps it
  through any number of future re-saves. Disqualified leads never get an `approval_status` at all
  (there is nothing to approve).
- **`list_leads()`** (lines 62-73) gained an `approval_status: str | None = None` parameter,
  applied as an additional optional `.where()` clause alongside the existing `status` filter — same
  pattern, same nullable-means-"don't filter" convention as the existing `status` parameter.
- **`set_approval_status(domain, approval_status)`** (lines 79-87) looks up the record, sets the new
  value, commits, and calls **`session.refresh(record)`** before returning it. The refresh matters
  because SQLAlchemy ORM objects don't automatically re-read from the database after a commit inside
  the same session in every configuration — without the explicit refresh, the caller could get back
  an object whose other fields (e.g. `last_seen_at`) reflect stale in-memory state rather than what
  was actually just persisted. Returns `None` when the domain doesn't exist, letting the API layer
  turn that into a 404 without a separate existence check.

### `app/api/leads.py`
- **`GET /v1/leads`** (`list_leads`, lines 122-134) gained an `approval_status:
  Literal["pending", "approved", "rejected", "sent"] | None = None` query parameter, passed straight
  through to `repository.list_leads()`. The `Literal` type means FastAPI rejects an invalid value
  with its own 422 before the handler even runs — no manual validation needed.
- **`POST /v1/leads/{domain}/approval`** (`decide_lead_approval`, lines 150-164): looks up the
  record (404 if missing), then requires `record.approval_status == "pending"` — anything else
  (already approved, already rejected, already sent, or never qualified) is a 400 with the current
  status in the message. Only then does it call `set_approval_status(domain, body.decision)`. This
  guard is what makes the endpoint idempotent-safe: n8n (or a retried Slack click) can't accidentally
  flip an already-decided lead a second time.
- **`POST /v1/leads/{domain}/sent`** (`mark_lead_sent`, lines 167-179): the mirror image — requires
  `record.approval_status == "approved"` (400 otherwise), then sets it to `"sent"`. This is the step
  workflow 02 calls right after Gmail confirms the send, so `"sent"` always means "the approval gate
  was cleared and outreach actually went out," not just "someone clicked approve."

### `app/observability/alerting.py`
- **`send_alert(settings, *, kind, status, error)`** is a single function, deliberately shaped like
  Phase 9's `traced_span()` no-op pattern: if `settings.n8n_alert_webhook_url` is unset, it returns
  immediately — no network call, nothing to mock in tests or CI, no behavior change for anyone not
  using n8n. When it is set, it does a bare `httpx.post(...)` with a 5-second timeout, and wraps the
  whole thing in `try/except Exception` that only logs a warning. **It never raises.** That's
  intentional: a failing alert (n8n down, webhook URL wrong, network blip) must never become a
  second failure on top of the job failure that triggered it in the first place — the job is already
  marked `failed`; losing the alert is a degraded experience, not a crash.

### `app/api/jobs.py`
- **`JobStore.mark_failed(job_id, error, settings=None)`** (lines 47-54) gained one new optional
  parameter, `settings: Settings | None = None`, defaulting to `None` — every pre-existing call site
  and every pre-existing test (`tests/api/test_jobs.py::test_mark_failed_sets_error_and_finished_at`,
  which calls `store.mark_failed(job.job_id, "boom")` with no `settings`) keeps working unmodified.
  Only when `settings is not None` does it call `send_alert(settings, kind=job.kind, status="failed",
  error=error)` — the two real call sites, `app/api/leads.py`'s `_run_lead_job` and
  `_run_discovery_job`, both now pass `settings` through, so real job failures do trigger an alert,
  but nothing else in the codebase (or the test suite) needed to change to keep working.

### `n8n/` — the three workflow files
- **`01-trigger-ingestion.json`** — one webhook (`/trigger-lead`) that inspects the incoming body
  and routes to `POST /v1/discovery` (body has `query`/`queries`) or `POST /v1/leads` (otherwise),
  then echoes the app's response back through `Respond to Webhook`.
- **`02-approval-and-send.json`** — the two independent chains described in section 2: a Schedule
  Trigger that polls `GET /v1/leads?approval_status=pending` every 5 minutes and posts each draft to
  Slack with Approve/Reject buttons, and a separate Webhook that receives the button callback,
  records the decision via `POST .../approval`, and — only on approval — fetches the full lead,
  sends it through Gmail, and marks it sent via `POST .../sent`.
- **`03-alerting.json`** — a single webhook (`/alert`) that receives the app's `send_alert()` POST
  and forwards it to a Slack `#ops-alerts` channel.
- All three share the same shape: `"active": false` (nothing runs until a human imports and
  activates them), an `httpHeaderAuth` credential referenced wherever they call the app (`X-API-Key`
  header), and a `meta.notes` field on the workflow itself documenting exactly what credentials it
  needs and stating plainly that it was **not imported or run as part of Phase 10**.

### `deploy/docker-compose.yml`
- A new `n8n` service (`n8nio/n8n` image) joins `db` and `app`, exposing port `5678` and persisting
  its data (workflows, credentials, execution history) in a named volume `n8n_data:/home/node/.n8n`
  — so imported workflows and configured credentials survive a container restart. It has no
  `depends_on` on `app` because n8n itself doesn't need the app to be up to start; the workflows it
  would run do need the app, but that's a runtime concern, not a startup-ordering one.

---

## 4. Key concepts (transferable)

| Concept | In one line | When to reach for it |
|---------|-------------|----------------------|
| State machine as a nullable column | `approval_status` lives on the same row it describes, not in a joined table — one source of truth for a simple enum-like state | Any entity with exactly one current state at a time (order status, ticket status, approval status) where a join would only ever answer "what's the current value" |
| Push vs. pull for cross-system notification | The app calls n8n's webhook the instant a failure happens, instead of n8n polling a metrics endpoint and diffing counters | Any time the producer of an event can make an outbound call — push is simpler and immediate; reach for polling only when the producer can't be modified or the consumer can't expose an inbound endpoint |
| "Never overwrite a human decision" idempotency in an upsert | `save()`'s upsert only sets `approval_status = "pending"` when the column is `None`; any existing value (human-set or otherwise) survives every future re-save | Any upsert that writes over a field a human (or another system) may have already set — guard the write with "only if unset," not "always set the default" |
| Orchestration-layer vs. application-layer separation of concerns | n8n owns routing, human interaction (Slack buttons), and sending (Gmail); the Python app owns research/qualification/drafting and is the sole source of truth for lead data | Any system that combines a low-code workflow tool with a custom application — draw the line at "who talks to humans and external services" vs. "who does the domain-specific work," and don't let either side duplicate the other |

---

## 5. How to run & test it

```bash
./.venv/Scripts/python.exe -m pytest tests/db/test_models.py tests/db/test_repository.py \
  tests/api/test_leads.py tests/api/test_jobs.py -v

# Full suite
./.venv/Scripts/python.exe -m pytest -q
```

### What the tests prove
These are the tests that exist for this phase — all from Tasks 1-4, the `approval_status` data model
and API layer (see the honest note in section 1: Tasks 5-8 have no new automated tests).

- `tests/db/test_models.py::test_lead_record_approval_status_column_defaults_to_none` — a freshly
  inserted `LeadRecord` has `approval_status is None`.
- `tests/db/test_repository.py` —
  `test_save_sets_pending_approval_status_for_new_qualified_lead`,
  `test_save_never_sets_approval_status_for_disqualified_lead`, and
  `test_save_does_not_overwrite_existing_approval_status_on_resave` together pin down the
  pending-on-first-save-only rule from section 1; `test_list_leads_filters_by_approval_status`,
  `test_set_approval_status_updates_and_returns_the_record`, and
  `test_set_approval_status_returns_none_for_unknown_domain` cover the new repository methods.
- `tests/api/test_leads.py` — `test_list_leads_filters_by_approval_status` covers the new query
  parameter; `test_decide_lead_approval_approves_a_pending_lead`,
  `test_decide_lead_approval_rejects_a_pending_lead`,
  `test_decide_lead_approval_404_for_unknown_domain`, and
  `test_decide_lead_approval_400_when_not_pending` cover `POST .../approval`;
  `test_mark_lead_sent_marks_an_approved_lead_as_sent`,
  `test_mark_lead_sent_404_for_unknown_domain`, and `test_mark_lead_sent_400_when_not_approved`
  cover `POST .../sent`.
- `tests/api/test_jobs.py` — unchanged from Phase 9; `test_mark_failed_sets_error_and_finished_at`
  still calls `mark_failed()` with no `settings` argument, confirming the new parameter is fully
  backward compatible. There is no new test asserting `send_alert()` actually fires on failure —
  that wiring was verified by reading `app/api/jobs.py:53-54` and `app/observability/alerting.py`
  directly, plus the full-suite regression run.

### Bringing up the full stack

```bash
docker compose -f deploy/docker-compose.yml up --build
```

This now brings up three services: `db` (Postgres), `app` (the FastAPI service on `:8000`), and
`n8n` (on `http://localhost:5678`). Open `localhost:5678` to reach n8n's own setup/login screen.

### What this phase deliberately didn't do

Actually importing the three files under `n8n/` into that running n8n instance, configuring the
`httpHeaderAuth` / Slack / Gmail credentials each workflow's `meta.notes` field calls for, activating
the workflows, and running one real ingestion → approval → send loop end to end is a manual step —
by design, not an oversight (see the scope-boundary note in section 1). That's the user's own next
step, whenever they choose to do it.

---

## 6. What's next

Phase 11 — **Deploy**, simplified in scope from the original plan: docker-compose only (setup
scripts plus deployment docs living in the repo), with Kubernetes/minikube and the Supabase-managed-
Postgres migration documented as a future path rather than actually built out. After that, the
project concludes with a single comprehensive documentation page covering the whole build, published
as a shareable link — replacing the originally-planned Phase 12 ("Quality + polish") entirely.
