# Phase 9: Observability — Design Spec

**Date:** 2026-07-16
**Status:** Approved design (pre-implementation)

---

## 1. Purpose & Context

Every phase so far has been debugged by reading `print`/`logging.info` output and manually
verifying test suites. That's worked, but it doesn't scale past a solo developer staring at a
terminal, and it's exactly the gap between "a script that works" and "a production-grade service" —
the second half of this project's stated goal from Phase 1.

Phase 9 closes that gap with the three pillars the original Phase-1 design spec's §9 called for,
finally wired up: **Langfuse tracing** (see what an agent actually did, step by step, with real
token/cost/latency), **Prometheus metrics** (is the service healthy, are runs succeeding), and
**structured JSON logging** (logs that are actually parseable once this runs somewhere other than
a local terminal). `Settings` has carried unused `langfuse_enabled`/`langfuse_public_key`/
`langfuse_secret_key`/`langfuse_host` fields since Phase 1 — this phase is what finally uses them.

All integration details below (Langfuse SDK v3 APIs, the OpenAI-compatible drop-in wrapper, the
Anthropic OpenTelemetry instrumentor, exact package names) were verified against Langfuse's current
documentation during this design's brainstorm, not assumed from memory — a discipline this project
already learned the hard way in Phase 3 (an incorrect "OpenRouter-only" native-search assumption
that had to be corrected after checking live SDK signatures).

## 2. Scope

### In scope (Phase 9)
- Langfuse tracing across **all four** LLM providers (OpenRouter, NVIDIA, OpenAI, Anthropic) —
  not just the one currently configured — matching this project's "swap provider via ENV, nothing
  else breaks" philosophy.
- One Langfuse trace per `LeadOrchestratorAgent.run()`, with nested spans for research/qualify/
  draft; LLM calls inside them auto-captured as child generations by the provider-level
  instrumentation.
- A `GET /metrics` Prometheus endpoint: request count + latency histogram (via middleware, labeled
  by path/method/status) and a job-outcome counter (labeled by kind and final status) driven by
  `JobStore`'s existing state transitions.
- Structured JSON logging: a custom `logging.Formatter`, a request-ID middleware (via
  `contextvars`), replacing the ad-hoc `logging.basicConfig` currently in
  `app/providers/llm/factory.py`.
- A single `setup_observability(settings)` entry point called from both `create_app()` (API) and
  every CLI script's `main()`, so tracing/logging behave identically regardless of entry point.

### Out of scope (deferred)
- Per-provider LLM token/cost counters as Prometheus metrics — Langfuse already computes this
  per-trace; duplicating it as a second metrics system isn't worth building twice.
- Any Grafana dashboard or actual Prometheus server configuration — this phase only exposes
  `/metrics`; wiring a real scraper/dashboard is Phase 11 (Deploy) territory.
- Auth on `/metrics` — stays open/unauthenticated, matching `/health`/`/ready`'s existing
  precedent (Prometheus scrapers don't easily send custom headers; metrics data itself isn't
  sensitive the way lead/outreach data is).

## 3. Langfuse Tracing

### Config bridge (`app/observability/tracing.py`)
Langfuse's Python SDK (v3, OpenTelemetry-based) reads credentials from `os.environ`, not from
parameters passed to `get_client()`. This project's config lives in `Settings`
(`langfuse_public_key`, `langfuse_secret_key`, `langfuse_host`), loaded from `.env`. `tracing.py`
bridges the two: when `settings.langfuse_enabled` is true, it sets
`os.environ["LANGFUSE_PUBLIC_KEY"]`/`["LANGFUSE_SECRET_KEY"]` from `Settings`, and sets **both**
`os.environ["LANGFUSE_HOST"]` and `os.environ["LANGFUSE_BASE_URL"]` to `settings.langfuse_host` —
current docs show the SDK reading `LANGFUSE_BASE_URL`, but this project's `.env` already has
`LANGFUSE_HOST` set from Phase 1; setting both is a cheap way to not depend on which name the
installed SDK version actually reads. Then calls `get_client()` once and returns it. When
`langfuse_enabled` is false, returns `None` — every call site below checks for `None` and no-ops.

### Per-provider instrumentation
- **OpenRouter/NVIDIA/OpenAI** (`app/providers/llm/openai_compatible.py`): the existing
  `from openai import OpenAI` is already a local import inside `OpenAICompatibleProvider.__init__`.
  Becomes conditional: `from langfuse.openai import OpenAI` when `langfuse_enabled`, else the
  plain `from openai import OpenAI` — a true drop-in swap (same class shape), zero changes to
  `complete()`/`complete_native_search()`.
- **Anthropic** (`app/providers/llm/anthropic_provider.py`): when `langfuse_enabled`,
  `setup_observability()` calls `AnthropicInstrumentor().instrument()` once at process startup
  (from the new `opentelemetry-instrumentation-anthropic` package) — this auto-wraps every
  subsequent `client.messages.create()` call project-wide via OpenTelemetry, no changes needed
  inside `AnthropicProvider` itself.

### Trace boundary
One trace per `LeadOrchestratorAgent.run()` call — this is the natural "one company researched"
unit a user would want to inspect. Implemented via
`langfuse.start_as_current_observation(as_type="span", name="lead-orchestrator-run")` wrapping the
body of `run()`, with nested child spans (same context manager, `as_type="span"`) around the
research/qualify/draft steps for readability in the Langfuse UI. A discovery sweep naturally
produces one trace per candidate researched (since it calls `orchestrator.run()` once per
candidate) — no special-casing needed for the sweep case.

## 4. Prometheus Metrics

- **`app/observability/metrics.py`**: defines a request `Counter` (labels: method, path, status),
  a request latency `Histogram` (labels: method, path), and a job-outcome `Counter` (labels: kind,
  status). Uses its own dedicated `CollectorRegistry` (not `prometheus_client`'s global default) —
  a deliberate choice to avoid a real `prometheus_client` gotcha where re-importing a module that
  defines a `Counter`/`Histogram` at module level (as pytest does across test files) raises a
  "duplicated timeseries" error against the global registry.
- **Middleware** (`app/main.py`): wraps every request, records method/path/status/duration into the
  two request-level metrics.
- **Job-outcome recording**: `JobStore.mark_done`/`mark_failed` (Phase 8) gain a call to increment
  the job-outcome counter — the only change needed to `app/api/jobs.py`.
- **`GET /metrics`**: added to the existing `app/api/health.py` router (already unauthenticated,
  already the home for `/health`/`/ready`), returning `generate_latest(registry)` with the correct
  Prometheus content type.

## 5. Structured JSON Logging

- **`app/observability/logging_config.py`**: a `logging.Formatter` subclass emitting one JSON
  object per log line (`timestamp`, `level`, `logger`, `message`, and `request_id` when present in
  context). No new dependency — built on stdlib `logging`.
- **Request-ID middleware** (`app/main.py`): generates a UUID per incoming request, stores it in a
  `contextvars.ContextVar`, and clears it after the response — so any `logging.info(...)` call
  anywhere in the call stack (including deep inside `discovery_pipeline.py`'s existing log calls)
  automatically includes the current request's ID without threading it through every function
  signature.
- `configure_logging()` (called once by `setup_observability()`) replaces the ad-hoc
  `logging.basicConfig(level=logging.INFO)` currently in `app/providers/llm/factory.py` — that line
  is removed; the root logger's handler/formatter is now configured centrally instead.

## 6. New Dependencies

`langfuse`, `opentelemetry-instrumentation-anthropic`, `prometheus-client` — exact version floors
to be pinned when the implementation plan is written (checking current stable releases at that
time, not guessed now).

## 7. Testing

- Langfuse/Anthropic-instrumentor code paths only activate when `settings.langfuse_enabled=True`;
  every test keeps it at its default (`False`), so the test suite never needs real Langfuse
  credentials or makes a real network call to Langfuse's servers.
- `metrics.py`'s dedicated `CollectorRegistry` (not the global default) means metrics tests can
  freely construct fresh registries per test without polluting global state or hitting duplicate-
  registration errors across the test run.
- Request-ID middleware and the JSON formatter are tested directly: a formatted log record parses
  as valid JSON and contains the expected keys; the middleware sets a distinguishable ID per
  request and clears it afterward.

## 8. What's Next

Phase 10 — **n8n integration** (ingestion, human-approval sending, alerting) — the alerting piece
there will likely want to react to the job-outcome metrics/logs this phase produces (e.g. alert on
a spike in `failed` outcomes), but wiring that reaction is Phase 10's job, not this one's.
