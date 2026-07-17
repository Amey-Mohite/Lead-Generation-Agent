# Phase 9 — Observability (Learning Guide)

> **Goal of this phase:** stop flying blind. Give every LLM call a trace, every request a metric,
> and every log line a request ID — without touching the shape of the pipeline itself.

---

## 1. What & why

`Settings` has carried `langfuse_enabled`, `langfuse_public_key`, `langfuse_secret_key`, and
`langfuse_host` since Phase 1 (`app/config.py`) — four fields nothing ever read. Phase 9 is the
phase that finally wires them up: real Langfuse tracing, Prometheus metrics, and structured JSON
logging, all through one shared entry point (`setup_observability()`) that both the API and the
CLI scripts call.

**Why Prometheus metrics use a dedicated `CollectorRegistry`, not the global default registry.**
`prometheus_client`'s module-level default registry raises a duplicate-timeseries error the second
time a `Counter`/`Histogram` with the same name is registered against it. Tests import
`app/observability/metrics.py` repeatedly across many test files (`tests/observability/test_metrics.py`,
`tests/api/test_jobs.py`, `tests/test_health.py`, …) — with the default registry, the second test
file to import the module would crash the whole suite. `app/observability/metrics.py:3` creates its
own `registry = CollectorRegistry()` and registers `REQUEST_COUNT`, `REQUEST_LATENCY`, and
`JOB_OUTCOMES` against *that* instead — module-level singletons (Python only executes the module
body once), so repeated imports are safe, and `/metrics` (`app/api/health.py`) reads from that same
dedicated registry rather than the process-wide default.

**Why `/metrics` stays unauthenticated.** This matches a pattern the project already established in
Phase 1/8: `/health` and `/ready` sit on their own router, outside `require_api_key`, because
liveness/readiness probes can't send a custom header. Prometheus scrapers have the exact same
constraint — a scrape config would need its own auth wiring for one endpoint, which is friction
standard Prometheus deployments don't expect. `/metrics` joins `/health`/`/ready` on the same
unauthenticated `health` router (`app/api/health.py`) for the same reason.

**Why one Langfuse trace per `LeadOrchestratorAgent.run()`, not per LLM call or per HTTP request.**
A single `run()` call is "research this one company, qualify it, maybe draft outreach" — the
natural unit a human reviewing traces actually wants to look at (did *this* company's research
brief lead to *this* qualification score and *this* draft, in one place). Tracing at the HTTP
request level would be too coarse (one `POST /v1/discovery` call fans out into many `run()`s across
many candidates) and tracing at the LLM-call level alone, without a parent span, would leave every
generation as an disconnected root with no way to see the qualify → draft causality. So
`orchestrator_agent.py` wraps the whole `run()` in an outer `lead-orchestrator-run` span, with
`research`/`qualify`/`draft` as nested children — and the individual LLM generations inside each of
those get auto-captured as descendants of whichever span is currently open, for free, by the
provider-level instrumentation described below.

---

## 2. The flow

```
  create_app() / scripts/try_lead.py / scripts/try_discovery.py
        │
        ▼
  setup_observability(settings)
        │
        ├─ configure_logging()                     -- JSON formatter on the root logger, always
        ├─ _instrument_anthropic_once()             -- only if langfuse_enabled (AnthropicInstrumentor,
        │                                              @lru_cache -- process-wide, exactly once)
        └─ get_langfuse_client(settings)            -- None if langfuse_enabled is False

  Per HTTP request (app/main.py):
    add_request_id middleware   -- request_id_var.set(uuid4()), reset in `finally`
          │
          ▼
    record_request_metrics middleware -- times the handler, calls record_request() after
          │
          ▼
    route handler (health / leads / jobs / discovery)

  Per LeadOrchestratorAgent.run() (app/agents/orchestrator_agent.py):
    traced_span(client, "lead-orchestrator-run")        <- outer span (no-op if client is None)
      traced_span(client, "research")   -> research_agent.run(target)
      traced_span(client, "qualify")    -> self._qualify(brief)      -- LLM generation auto-captured
      [only if qualification.score >= min_score_to_draft]
      traced_span(client, "draft")      -> self._draft(brief, qualification)  -- LLM generation too

  LLM-provider-level instrumentation (independent of the spans above, captures the generations
  that happen *inside* whichever span is currently open):
    OpenAI-compatible (openrouter/nvidia/openai) -- `from langfuse.openai import OpenAI` swapped in
                                                     for the real `openai.OpenAI` when langfuse_enabled
    Anthropic                                    -- global AnthropicInstrumentor().instrument(),
                                                     patches the SDK once for the whole process
```

Two independent tracing mechanisms cooperate here: the **spans** in `orchestrator_agent.py` give
Langfuse the "research → qualify → draft" structure, and the **provider-level instrumentation** (the
`langfuse.openai` import swap, or the global Anthropic instrumentor) captures the actual LLM
generations — prompts, completions, token counts, latency — nested inside whichever span happens to
be open when the call fires. Neither one needs to know about the other.

---

## 3. File-by-file walkthrough

### `app/observability/logging_config.py`
- **`request_id_var`** is a `contextvars.ContextVar[str | None]`, not a global variable or a value
  threaded through every function's parameter list. `contextvars` propagates correctly across
  `async`/`await` boundaries and concurrent requests within the same process — a global `str`
  variable would leak one request's ID into another's log lines under any concurrency, and
  threading an explicit `request_id: str` parameter through every function in the call graph
  (research agent, LLM providers, repository, exporters…) would mean touching nearly every
  signature in the codebase just to plumb one string through. A `ContextVar` lets any log call
  anywhere pick up the current request's ID implicitly, with no parameter changes anywhere.
- **`JSONLogFormatter.format()`** builds a `dict` (`timestamp`, `level`, `logger`, `message`, plus
  `request_id` only when one is set) and returns `json.dumps(payload)` — every log line is a single
  JSON object, which is what log aggregators (anything ingesting stdout in production) expect.
- **`configure_logging()`** clears any existing handlers on the root logger and attaches one
  `StreamHandler` using `JSONLogFormatter`. Clearing first matters because `setup_observability()`
  can run more than once in a process's lifetime in some code paths (e.g. re-imported test modules)
  — without clearing, re-running it would double up handlers and duplicate every log line.

### `app/observability/metrics.py`
- **The three metrics**: `REQUEST_COUNT` (`Counter`, labels `method`/`path`/`status`) and
  `REQUEST_LATENCY` (`Histogram`, labels `method`/`path`) cover the HTTP layer; `JOB_OUTCOMES`
  (`Counter`, labels `kind`/`status`) covers the background-job layer added in Phase 8 — a job can
  finish long after its triggering HTTP request, so it needs its own outcome metric rather than
  reusing the request counter.
- **Why a dedicated registry** — covered in section 1 above; see `registry = CollectorRegistry()`
  at the top of the file, passed explicitly to every metric's `registry=` kwarg and to
  `generate_latest(registry)` in `app/api/health.py`'s `/metrics` route.
- **`record_request()`** and **`record_job_outcome()`** are the only two functions anything else in
  the codebase calls — `app/main.py`'s middleware calls the former after every response,
  `app/api/jobs.py`'s `JobStore.mark_done()`/`mark_failed()` call the latter.

### `app/observability/tracing.py`
- **`get_langfuse_client(settings)`** returns `None` immediately if `settings.langfuse_enabled` is
  `False` — no environment mutation, no import of `langfuse` at all in the disabled case. When
  enabled, it copies `langfuse_public_key`/`langfuse_secret_key`/`langfuse_host` from `Settings`
  into `os.environ`, because the `langfuse` SDK's `get_client()` reads its configuration from
  environment variables, not from a config object passed in directly — this function is the bridge
  between the app's own `Settings`-based configuration and the SDK's env-var-based configuration.
- **Why both `LANGFUSE_HOST` and `LANGFUSE_BASE_URL` get set** — different versions/paths of the
  Langfuse SDK and its OpenTelemetry-based integrations read one or the other name for "which
  Langfuse instance to send traces to"; setting both from the same `settings.langfuse_host` value
  means the app doesn't need to know which name the currently-installed SDK version prefers.
- **`traced_span(client, name)`** is a context manager with a **no-op-when-disabled** pattern: if
  `client is None` (Langfuse disabled), it just `yield`s — the wrapped code runs exactly as if the
  `with traced_span(...)` line weren't there, no Langfuse import, no network call, nothing to mock
  out in tests that don't care about tracing. Only when a real client is passed does it delegate to
  `client.start_as_current_observation(as_type="span", name=name)`.

### `app/observability/setup.py`
- **`setup_observability(settings)`** is the single entry point everything else calls: it always
  runs `configure_logging()`, conditionally instruments Anthropic, and always returns
  `get_langfuse_client(settings)` (which itself may be `None`). Callers get one function instead of
  needing to know about three separate observability subsystems.
- **`_instrument_anthropic_once()`** is decorated with `@lru_cache` (no arguments, so it caches a
  single result) specifically so `AnthropicInstrumentor().instrument()` — which globally monkey-patches
  the `anthropic` SDK's client classes — runs at most once per process, no matter how many times
  `setup_observability()` is called. Instrumenting twice isn't just wasteful; OpenTelemetry
  instrumentors are generally not designed to be applied repeatedly to the same target and can
  raise or double-emit spans if you try.

### `app/providers/llm/openai_compatible.py`
- The constructor's provider-selection logic (lines ~19–26) is the "drop-in swap": when a real
  client isn't injected (`client=None`, the production path) and `langfuse_enabled` is `True`, it
  imports `OpenAI` **from `langfuse.openai`** instead of the plain `openai` package — same class
  name, same constructor signature, same `.chat.completions.create(...)` / `.responses.create(...)`
  call surface, but Langfuse's wrapped version auto-captures every call as a generation. When
  `langfuse_enabled` is `False`, it imports the real `openai.OpenAI` — zero behavioral difference,
  zero Langfuse import, for anyone running without tracing. This is the one provider file that
  needed a code change; `AnthropicProvider` needed none, because Anthropic's instrumentation is
  global (see below) rather than an import swap.

### `app/agents/orchestrator_agent.py`
- `LeadOrchestratorAgent.__init__` now accepts an optional `langfuse_client=None`, stored as
  `self._langfuse_client`.
- `run()` wraps its whole body in `with traced_span(self._langfuse_client, "lead-orchestrator-run")`,
  then nests `with traced_span(self._langfuse_client, "research")`,
  `with traced_span(self._langfuse_client, "qualify")`, and — **only reached when
  `qualification.score >= self._min_score_to_draft`** — `with traced_span(self._langfuse_client,
  "draft")`. When the lead is disqualified, the function returns from inside the `qualify` span's
  `with` block before the `draft` span is ever entered, so a disqualified run produces exactly three
  spans (`lead-orchestrator-run`, `research`, `qualify`), not four — this is asserted directly in
  `tests/agents/test_orchestrator_agent.py::test_run_skips_draft_span_when_disqualified`.
- `build_lead_orchestrator_agent(settings)` calls `get_langfuse_client(settings)` and passes the
  result (real client or `None`) straight into the constructor — the same function
  `setup_observability()` uses internally, called again here because building an orchestrator agent
  doesn't otherwise go through `setup_observability()`.

---

## 4. Key concepts (transferable)

| Concept | In one line | When to reach for it |
|---------|-------------|----------------------|
| No-op-by-default cross-cutting instrumentation | `traced_span()` degrades to a plain `yield` when tracing is disabled — the wrapped code path is identical either way | Any cross-cutting concern (tracing, feature flags, A/B variants) that must never change behavior or require credentials in tests/CI |
| `contextvars` for implicit per-request context | A `ContextVar` set once per request, read anywhere down the call stack without a parameter | Per-request identifiers (request ID, tenant ID, trace ID) that would otherwise have to be threaded through every function signature |
| Dedicated metrics registry for test isolation | Create your own `CollectorRegistry()` instead of registering against Prometheus's global default | Any metrics code that a test suite will import more than once across multiple test files |
| Drop-in SDK wrapper vs. global auto-instrumentation | Two different shapes for the same goal: swap one import (`langfuse.openai.OpenAI`) vs. monkey-patch a whole SDK once (`AnthropicInstrumentor().instrument()`) | Pick the wrapper-swap when the vendor ships one (simpler, more explicit); fall back to global instrumentation when they don't, or when the SDK doesn't offer an import-compatible wrapped client |
| `@lru_cache` for "run this exactly once" | A zero-argument function decorated with `@lru_cache` executes its body once per process and returns the cached result on every later call | Global mutations (monkey-patching, SDK instrumentation, one-time registrations) that must not be repeated even if the setup function is called many times |
| One trace span per meaningful unit of work | Nest spans to match how a human would narrate the work ("research, then qualify, then maybe draft"), not to match request/response boundaries | Multi-step pipelines where you want to see the whole story in one place when debugging, not scattered disconnected spans |

---

## 5. How to run & test it

```bash
./.venv/Scripts/python.exe -m pytest tests/observability tests/api/test_jobs.py \
  tests/providers/test_openai_compatible.py tests/agents/test_orchestrator_agent.py -v

# Full suite
./.venv/Scripts/python.exe -m pytest -q
```

### What the tests prove
- `tests/observability/test_logging_config.py` — `JSONLogFormatter.format()` produces valid JSON
  with the expected keys and omits `request_id` when unset; includes it when
  `request_id_var.set(...)` has been called.
- `tests/observability/test_metrics.py` — `record_request()` and `record_job_outcome()` each
  increment the correct labeled counter.
- `tests/observability/test_tracing.py` — `get_langfuse_client()` returns `None` when
  `langfuse_enabled=False`; `traced_span(None, ...)` still runs the wrapped code (no-op); a fake
  client's `start_as_current_observation(as_type="span", name=...)` is called with the right
  arguments when a client is present.
- `tests/observability/test_setup.py` — `setup_observability()` returns `None` when Langfuse is
  disabled (no Anthropic instrumentation, no client).
- `tests/api/test_jobs.py` — `test_mark_done_records_job_outcome_metric` /
  `test_mark_failed_records_job_outcome_metric` confirm `JobStore.mark_done`/`mark_failed` actually
  bump `JOB_OUTCOMES`.
- `tests/providers/test_openai_compatible.py` —
  `test_langfuse_enabled_flag_does_not_affect_behavior_when_client_given` proves the `langfuse_enabled`
  flag is inert whenever a `client` is injected (the test-double path), so the import-swap branch
  only ever fires in the real, non-test constructor path.
- `tests/agents/test_orchestrator_agent.py` — a fake Langfuse client records span names into a
  list; `test_run_creates_traced_spans_when_langfuse_client_provided` asserts the full
  `["lead-orchestrator-run", "research", "qualify", "draft"]` sequence for a qualified lead, and
  `test_run_skips_draft_span_when_disqualified` asserts the `draft` span is absent for a
  disqualified one; `test_build_lead_orchestrator_agent_has_no_langfuse_client_when_disabled`
  confirms `build_lead_orchestrator_agent()` wires `_langfuse_client` to `None` when
  `langfuse_enabled=False`.
- `tests/test_health.py` — `test_metrics_endpoint_returns_prometheus_format` hits `GET /metrics`
  through a real `TestClient` and asserts both `http_requests_total` and `job_outcomes_total` show
  up in the Prometheus text exposition format.

### Trying it for real

**See real Prometheus output:**

```bash
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload
curl http://localhost:8000/metrics
```

Every request you make (including the `/metrics` scrape itself, once it's recorded) shows up as
`http_requests_total{method="GET",path="/metrics",status="200"} ...` and a
`http_request_duration_seconds` histogram; trigger a `POST /v1/leads` or `/v1/discovery` run and let
it finish to see `job_outcomes_total{kind="lead",status="done"}` appear too.

**See a real Langfuse trace:**

1. Set `LANGFUSE_ENABLED=true`, plus real `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` (and
   `LANGFUSE_HOST` if self-hosting instead of Langfuse Cloud) in `.env`.
2. Run `./.venv/Scripts/python.exe scripts/try_lead.py <target>` with a real LLM API key configured
   (a real run, not `--demo` — the scripted demo path never calls a real LLM provider, so there's
   nothing to trace).
3. Open your Langfuse project's dashboard — a `lead-orchestrator-run` trace appears with nested
   `research`/`qualify`/`draft` spans, and (for OpenAI-compatible providers, or Anthropic) the
   individual LLM generations nested inside them with prompts, completions, and token usage.

---

## 6. What's next

Phase 10 — **n8n integration** (ingestion, human-approval sending, alerting). Alerting there will
likely want to react to exactly the signals this phase produces: the `job_outcomes_total{status="failed"}`
metric and the structured JSON logs are the natural inputs for "notify someone when a lead run
fails," rather than inventing a separate failure-reporting mechanism from scratch.
