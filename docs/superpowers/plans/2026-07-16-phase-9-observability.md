# Phase 9: Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

> **Execution note:** The user commits/pushes to GitHub themselves. Do **not** run `git commit`
> or `git push`. End each task by reporting exactly what changed for the user to review and
> commit.

**Goal:** Wire up the three observability pillars this project has carried unused config for since
Phase 1 — Langfuse tracing (one trace per lead run, across all four LLM providers), Prometheus
metrics (`GET /metrics`: request counts/latency, job outcomes), and structured JSON logging with
request IDs — all through one `setup_observability(settings)` entry point shared by the API and
every CLI script.

**Architecture:** A new `app/observability/` package (`logging_config.py`, `metrics.py`,
`tracing.py`, `setup.py`). Langfuse integrates per-provider: `OpenAICompatibleProvider` (covers
OpenRouter/NVIDIA/OpenAI) gets a conditional import swap to `langfuse.openai.OpenAI`; Anthropic
gets a one-time global `AnthropicInstrumentor().instrument()` call. `LeadOrchestratorAgent.run()`
wraps itself in a manual span so LLM generations captured by the provider-level instrumentation
above nest inside a single "lead-orchestrator-run" trace. Prometheus metrics use their own
dedicated `CollectorRegistry`, not the library's global default.

**Tech Stack:** `langfuse>=4.10` (confirmed current: 4.14.0 on PyPI at plan-writing time),
`opentelemetry-instrumentation-anthropic>=0.58` (confirmed current: 0.62.1),
`prometheus-client>=0.21` (confirmed current: 0.25.0). All three verified against live PyPI/docs
during this plan's design, not assumed from memory.

## Global Constraints

- **Python:** 3.12+.
- **No network/real Langfuse credentials in tests:** every Langfuse/Anthropic-instrumentor code
  path only activates when `settings.langfuse_enabled=True`; tests keep the default (`False`), so
  the suite never needs real credentials or makes a real network call.
- **Prometheus metrics use a dedicated `CollectorRegistry`**, never `prometheus_client`'s global
  default — avoids the well-known "duplicated timeseries" error when a module defining
  module-level `Counter`/`Histogram` objects gets imported repeatedly across test files.
- **`GET /metrics` stays unauthenticated**, added to the existing `app/api/health.py` router
  (same precedent as `/health`/`/ready`) — matches standard Prometheus scrape practice.
- **One Langfuse trace per `LeadOrchestratorAgent.run()`**, with nested spans for research/qualify/
  draft — not per-HTTP-request, not per-LLM-call alone.
- **`setup_observability(settings)` is the single entry point** — called from `create_app()` and
  from every CLI script's `main()`, so tracing/logging behave identically regardless of entry point.
- **Every task ends** with: tests green, then report the changes to the user for review/commit.

## File Structure

```
app/
  observability/
    __init__.py
    logging_config.py    # JSON formatter + request_id contextvar
    metrics.py             # dedicated CollectorRegistry + Counter/Histogram + record_*() helpers
    tracing.py              # Langfuse config bridge + traced_span() context manager
    setup.py                 # setup_observability(settings) -- the one entry point
  api/
    health.py               # + GET /metrics
    jobs.py                   # + record_job_outcome() wired into mark_done/mark_failed
  providers/llm/
    openai_compatible.py     # + conditional langfuse.openai import
    factory.py                 # + langfuse_enabled passed through; ad-hoc basicConfig removed
  agents/
    orchestrator_agent.py      # + nested traced_span() calls in run()
  main.py                       # + request-ID middleware, metrics middleware, setup_observability()
scripts/
  try_lead.py, try_discovery.py  # + setup_observability(settings) call
tests/
  observability/
    __init__.py, test_logging_config.py, test_metrics.py, test_tracing.py, test_setup.py
  api/ test_jobs.py, test_health.py   # appended
  providers/ test_openai_compatible.py  # appended
  agents/ test_orchestrator_agent.py     # appended
docs/
  learning/phase-9-observability.md
```

---

### Task 1: Structured JSON logging (formatter + request-ID middleware)

**Files:**
- Create: `app/observability/__init__.py` (empty), `app/observability/logging_config.py`
- Modify: `app/main.py`
- Test: `tests/observability/__init__.py` (empty), `tests/observability/test_logging_config.py`;
  append to `tests/test_health.py`

**Interfaces:**
- Consumes: stdlib `logging`, `contextvars`, `json`.
- Produces:
  - `request_id_var: contextvars.ContextVar[str | None]` (default `None`).
  - `JSONLogFormatter(logging.Formatter)` — `.format(record) -> str`, a JSON object with
    `timestamp`, `level`, `logger`, `message`, and `request_id` (only when set).
  - `configure_logging(level: int = logging.INFO) -> None` — clears root logger handlers, adds one
    `StreamHandler` using `JSONLogFormatter`.

- [ ] **Step 1: Write the failing test** — `tests/observability/test_logging_config.py`

```python
import json
import logging

from app.observability.logging_config import JSONLogFormatter, request_id_var


def test_format_produces_valid_json_with_expected_keys():
    formatter = JSONLogFormatter()
    record = logging.LogRecord(
        name="app.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello %s", args=("world",), exc_info=None,
    )
    output = formatter.format(record)
    payload = json.loads(output)

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "hello world"
    assert "timestamp" in payload
    assert "request_id" not in payload


def test_format_includes_request_id_when_set():
    formatter = JSONLogFormatter()
    token = request_id_var.set("req-123")
    try:
        record = logging.LogRecord(
            name="app.test", level=logging.WARNING, pathname=__file__, lineno=1,
            msg="careful", args=(), exc_info=None,
        )
        payload = json.loads(formatter.format(record))
        assert payload["request_id"] == "req-123"
    finally:
        request_id_var.reset(token)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/observability/test_logging_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.observability'`. Create the empty
`tests/observability/__init__.py` and `app/observability/__init__.py` first if collection errors.

- [ ] **Step 3: Create `app/observability/logging_config.py`**

```python
import contextvars
import json
import logging

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


class JSONLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id
        return json.dumps(payload)


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JSONLogFormatter())
    root.addHandler(handler)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/observability/test_logging_config.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Add the request-ID middleware to `app/main.py`**

```python
import uuid

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.leads import router as leads_router
from app.config import get_settings
from app.observability.logging_config import request_id_var


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.app_version)

    @app.middleware("http")
    async def add_request_id(request, call_next):
        token = request_id_var.set(str(uuid.uuid4()))
        try:
            return await call_next(request)
        finally:
            request_id_var.reset(token)

    app.include_router(health_router)
    app.include_router(leads_router)
    return app


app = create_app()
```

- [ ] **Step 6: Add a regression test** — append to `tests/test_health.py`:

```python
def test_health_ok_with_request_id_middleware_active():
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
```

- [ ] **Step 7: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all prior tests + this task's new tests green (156 total: 153 + 3 new: 2 in
`test_logging_config.py` + 1 in `test_health.py`).

- [ ] **Step 8: Report changes** to the user for review/commit.

---

### Task 2: Prometheus metrics

**Files:**
- Create: `app/observability/metrics.py`
- Modify: `app/main.py`, `app/api/health.py`, `app/api/jobs.py`, `pyproject.toml`
- Test: `tests/observability/test_metrics.py`; append to `tests/api/test_jobs.py`,
  `tests/test_health.py`

**Interfaces:**
- Consumes: `Job.kind` (`Literal["lead", "discovery"]`), `JobStore.mark_done`/`.mark_failed`
  (Task 1 of Phase 8, unchanged signatures).
- Produces:
  - `registry: CollectorRegistry` — dedicated, not the global default.
  - `record_request(method: str, path: str, status: int, duration_seconds: float) -> None`.
  - `record_job_outcome(kind: str, status: str) -> None`.
  - `GET /metrics` on the existing health router.

- [ ] **Step 1: Add the dependency** — in `pyproject.toml`, add `"prometheus-client>=0.21"` to
  `dependencies`.

- [ ] **Step 2: Install it**

Run: `./.venv/Scripts/python.exe -m pip install -q -e "."`
Expected: installs `prometheus-client`; exit 0.

- [ ] **Step 3: Write the failing test** — `tests/observability/test_metrics.py`

```python
from app.observability.metrics import JOB_OUTCOMES, REQUEST_COUNT, record_job_outcome, record_request


def test_record_request_increments_counter():
    before = REQUEST_COUNT.labels(method="GET", path="/v1/leads", status="200")._value.get()
    record_request(method="GET", path="/v1/leads", status=200, duration_seconds=0.05)
    after = REQUEST_COUNT.labels(method="GET", path="/v1/leads", status="200")._value.get()
    assert after == before + 1


def test_record_job_outcome_increments_counter():
    before = JOB_OUTCOMES.labels(kind="lead", status="done")._value.get()
    record_job_outcome(kind="lead", status="done")
    after = JOB_OUTCOMES.labels(kind="lead", status="done")._value.get()
    assert after == before + 1
```

- [ ] **Step 4: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/observability/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.observability.metrics'`.

- [ ] **Step 5: Create `app/observability/metrics.py`**

```python
from prometheus_client import CollectorRegistry, Counter, Histogram

registry = CollectorRegistry()

REQUEST_COUNT = Counter(
    "http_requests_total", "Total HTTP requests", ["method", "path", "status"],
    registry=registry,
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency in seconds", ["method", "path"],
    registry=registry,
)
JOB_OUTCOMES = Counter(
    "job_outcomes_total", "Background job outcomes", ["kind", "status"],
    registry=registry,
)


def record_request(method: str, path: str, status: int, duration_seconds: float) -> None:
    REQUEST_COUNT.labels(method=method, path=path, status=str(status)).inc()
    REQUEST_LATENCY.labels(method=method, path=path).observe(duration_seconds)


def record_job_outcome(kind: str, status: str) -> None:
    JOB_OUTCOMES.labels(kind=kind, status=status).inc()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/observability/test_metrics.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Wire job-outcome recording into `app/api/jobs.py`** — add the import and two calls:

```python
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel

from app.observability.metrics import record_job_outcome


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
        record_job_outcome(kind=job.kind, status="done")

    def mark_failed(self, job_id: str, error: str) -> None:
        job = self._jobs[job_id]
        job.status = "failed"
        job.error = error
        job.finished_at = datetime.now(timezone.utc)
        record_job_outcome(kind=job.kind, status="failed")

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)


@lru_cache
def get_job_store() -> JobStore:
    return JobStore()
```

- [ ] **Step 8: Add a test** — append to `tests/api/test_jobs.py`:

```python
from app.observability.metrics import JOB_OUTCOMES


def test_mark_done_records_job_outcome_metric():
    store = JobStore()
    job = store.create(kind="lead")
    before = JOB_OUTCOMES.labels(kind="lead", status="done")._value.get()
    store.mark_done(job.job_id, "result")
    after = JOB_OUTCOMES.labels(kind="lead", status="done")._value.get()
    assert after == before + 1


def test_mark_failed_records_job_outcome_metric():
    store = JobStore()
    job = store.create(kind="discovery")
    before = JOB_OUTCOMES.labels(kind="discovery", status="failed")._value.get()
    store.mark_failed(job.job_id, "boom")
    after = JOB_OUTCOMES.labels(kind="discovery", status="failed")._value.get()
    assert after == before + 1
```

- [ ] **Step 9: Add `GET /metrics` to `app/api/health.py`**

```python
from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.config import Settings, get_settings
from app.db.session import get_engine
from app.observability.metrics import registry

router = APIRouter(tags=["health"])


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    return {"status": "ok", "version": settings.app_version}


@router.get("/ready")
def ready() -> JSONResponse:
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503, content={"status": "not_ready", "database": "down"}
        )
    return JSONResponse(status_code=200, content={"status": "ready", "database": "up"})


@router.get("/metrics")
def metrics() -> Response:
    return Response(content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
```

- [ ] **Step 10: Add a test** — append to `tests/test_health.py`:

```python
def test_metrics_endpoint_returns_prometheus_format():
    client = TestClient(create_app())
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "http_requests_total" in resp.text
    assert "job_outcomes_total" in resp.text
```

- [ ] **Step 11: Add the request-metrics middleware to `app/main.py`**

```python
import time
import uuid

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.leads import router as leads_router
from app.config import get_settings
from app.observability.logging_config import request_id_var
from app.observability.metrics import record_request


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.app_version)

    @app.middleware("http")
    async def add_request_id(request, call_next):
        token = request_id_var.set(str(uuid.uuid4()))
        try:
            return await call_next(request)
        finally:
            request_id_var.reset(token)

    @app.middleware("http")
    async def record_request_metrics(request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        record_request(
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_seconds=time.perf_counter() - start,
        )
        return response

    app.include_router(health_router)
    app.include_router(leads_router)
    return app


app = create_app()
```

- [ ] **Step 12: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all green (161 total: 156 + 5 new: 2 in `test_metrics.py`, 2 in `test_jobs.py`, 1 in
`test_health.py`).

- [ ] **Step 13: Report changes** to the user for review/commit.

---

### Task 3: Langfuse tracing bridge

**Files:**
- Create: `app/observability/tracing.py`
- Modify: `pyproject.toml`
- Test: `tests/observability/test_tracing.py`

**Interfaces:**
- Consumes: `Settings` (`app.config`) — reads `langfuse_enabled`, `langfuse_public_key`,
  `langfuse_secret_key`, `langfuse_host`.
- Produces:
  - `get_langfuse_client(settings: Settings)` — returns a Langfuse client when
    `settings.langfuse_enabled` is true (bridging `Settings` to the SDK's env-var-native
    `get_client()`), else `None`.
  - `traced_span(client, name: str)` — a context manager; no-ops when `client` is `None`, else
    delegates to `client.start_as_current_observation(as_type="span", name=name)`.

- [ ] **Step 1: Add the dependency** — in `pyproject.toml`, add `"langfuse>=4.10"` to
  `dependencies`.

- [ ] **Step 2: Install it**

Run: `./.venv/Scripts/python.exe -m pip install -q -e "."`
Expected: installs `langfuse`; exit 0.

- [ ] **Step 3: Write the failing test** — `tests/observability/test_tracing.py`

```python
from app.config import Settings
from app.observability.tracing import get_langfuse_client, traced_span


def test_get_langfuse_client_returns_none_when_disabled():
    settings = Settings(_env_file=None, langfuse_enabled=False)
    assert get_langfuse_client(settings) is None


def test_traced_span_is_a_noop_when_client_is_none():
    executed = False
    with traced_span(None, "test-span"):
        executed = True
    assert executed is True


def test_traced_span_delegates_to_client_when_present():
    calls = []

    class _FakeObservation:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class _FakeClient:
        def start_as_current_observation(self, *, as_type, name):
            calls.append((as_type, name))
            return _FakeObservation()

    with traced_span(_FakeClient(), "research"):
        pass

    assert calls == [("span", "research")]
```

- [ ] **Step 4: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/observability/test_tracing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.observability.tracing'`.

- [ ] **Step 5: Create `app/observability/tracing.py`**

```python
import os
from contextlib import contextmanager
from typing import Iterator

from app.config import Settings


def get_langfuse_client(settings: Settings):
    if not settings.langfuse_enabled:
        return None

    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key or ""
    os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key or ""
    if settings.langfuse_host:
        os.environ["LANGFUSE_HOST"] = settings.langfuse_host
        os.environ["LANGFUSE_BASE_URL"] = settings.langfuse_host

    from langfuse import get_client

    return get_client()


@contextmanager
def traced_span(client, name: str) -> Iterator[None]:
    if client is None:
        yield
        return
    with client.start_as_current_observation(as_type="span", name=name):
        yield
```

- [ ] **Step 6: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/observability/test_tracing.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all green (164 total: 161 + 3 new).

- [ ] **Step 8: Report changes** to the user for review/commit.

---

### Task 4: `setup_observability()` entry point + Anthropic instrumentation

**Files:**
- Create: `app/observability/setup.py`
- Modify: `app/main.py`, `scripts/try_lead.py`, `scripts/try_discovery.py`,
  `app/providers/llm/factory.py`, `pyproject.toml`
- Test: `tests/observability/test_setup.py`

**Interfaces:**
- Consumes: `configure_logging` (Task 1), `get_langfuse_client` (Task 3), `Settings`.
- Produces: `setup_observability(settings: Settings)` — calls `configure_logging()`, instruments
  Anthropic (once, process-wide) when `settings.langfuse_enabled`, and returns
  `get_langfuse_client(settings)` (a client or `None`).

- [ ] **Step 1: Add the dependency** — in `pyproject.toml`, add
  `"opentelemetry-instrumentation-anthropic>=0.58"` to `dependencies`.

- [ ] **Step 2: Install it**

Run: `./.venv/Scripts/python.exe -m pip install -q -e "."`
Expected: installs `opentelemetry-instrumentation-anthropic` (and its OpenTelemetry dependencies);
exit 0.

- [ ] **Step 3: Write the failing test** — `tests/observability/test_setup.py`

```python
from app.config import Settings
from app.observability.setup import setup_observability


def test_setup_observability_returns_none_when_langfuse_disabled():
    settings = Settings(_env_file=None, langfuse_enabled=False)
    client = setup_observability(settings)
    assert client is None
```

- [ ] **Step 4: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/observability/test_setup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.observability.setup'`.

- [ ] **Step 5: Create `app/observability/setup.py`**

```python
from functools import lru_cache

from app.config import Settings
from app.observability.logging_config import configure_logging
from app.observability.tracing import get_langfuse_client


@lru_cache
def _instrument_anthropic_once() -> bool:
    from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor

    AnthropicInstrumentor().instrument()
    return True


def setup_observability(settings: Settings):
    configure_logging()
    if settings.langfuse_enabled:
        _instrument_anthropic_once()
    return get_langfuse_client(settings)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/observability/test_setup.py -v`
Expected: PASS (1 passed).

- [ ] **Step 7: Remove the ad-hoc `logging.basicConfig` from `app/providers/llm/factory.py`**

```python
from app.config import Settings
from app.providers.llm.anthropic_provider import AnthropicProvider
from app.providers.llm.base import LLMProvider
from app.providers.llm.openai_compatible import OpenAICompatibleProvider
import logging


_OPENAI_COMPATIBLE = {
    "openrouter": ("https://openrouter.ai/api/v1", "openrouter_api_key"),
    "nvidia": ("https://integrate.api.nvidia.com/v1", "nvidia_api_key"),
    "openai": (None, "openai_api_key"),
}


def build_llm_provider(settings: Settings) -> LLMProvider:
    provider = settings.llm_provider.lower()
    logging.info(f"Building LLM provider {provider} with model={settings.llm_model}")
    if provider in _OPENAI_COMPATIBLE:
        base_url, key_attr = _OPENAI_COMPATIBLE[provider]
        logging.info(f"Building OpenAI-compatible provider {provider} with base_url={base_url} and key_attr={key_attr}")
        return OpenAICompatibleProvider(
            name=provider,
            default_model=settings.llm_model,
            base_url=base_url,
            api_key=getattr(settings, key_attr),
        )

    if provider == "anthropic":
        return AnthropicProvider(
            default_model=settings.llm_model,
            api_key=settings.anthropic_api_key,
        )

    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")
```

(Only the `logging.basicConfig(level=logging.INFO)` line is removed — every other line is
unchanged. `import logging` stays since `logging.info(...)` calls remain.)

- [ ] **Step 8: Wire `setup_observability()` into `app/main.py`**

```python
import time
import uuid

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.leads import router as leads_router
from app.config import get_settings
from app.observability.logging_config import request_id_var
from app.observability.metrics import record_request
from app.observability.setup import setup_observability


def create_app() -> FastAPI:
    settings = get_settings()
    setup_observability(settings)
    app = FastAPI(title=settings.app_name, version=settings.app_version)

    @app.middleware("http")
    async def add_request_id(request, call_next):
        token = request_id_var.set(str(uuid.uuid4()))
        try:
            return await call_next(request)
        finally:
            request_id_var.reset(token)

    @app.middleware("http")
    async def record_request_metrics(request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        record_request(
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_seconds=time.perf_counter() - start,
        )
        return response

    app.include_router(health_router)
    app.include_router(leads_router)
    return app


app = create_app()
```

- [ ] **Step 9: Wire `setup_observability()` into `scripts/try_lead.py`** — in `main()`, right
  after `settings = get_settings()`:

```python
    settings = get_settings()
    from app.observability.setup import setup_observability

    setup_observability(settings)
    key_attr = _KEY_ATTR.get(settings.llm_provider)
```

- [ ] **Step 10: Wire `setup_observability()` into `scripts/try_discovery.py`** — same pattern, in
  `main()`, right after `settings = get_settings()`:

```python
    settings = get_settings()
    from app.observability.setup import setup_observability

    setup_observability(settings)
    key_attr = _KEY_ATTR.get(settings.llm_provider)
```

- [ ] **Step 11: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all green (165 total: 164 + 1 new).

- [ ] **Step 12: Manual smoke test**

Run: `./.venv/Scripts/python.exe scripts/try_lead.py --demo`
Expected: runs exactly as before (offline scripted demo), now printing JSON-formatted log lines
(e.g. `{"timestamp": "...", "level": "INFO", "logger": "root", "message": "..."}`) instead of plain
text, since `LANGFUSE_ENABLED` is false in `.env` by default and `configure_logging()` is always
called regardless.

- [ ] **Step 13: Report changes** to the user for review/commit.

---

### Task 5: Langfuse wiring into `OpenAICompatibleProvider`

**Files:**
- Modify: `app/providers/llm/openai_compatible.py`, `app/providers/llm/factory.py`
- Test: append to `tests/providers/test_openai_compatible.py`

**Interfaces:**
- Consumes: nothing new from earlier tasks (this only needs `langfuse` installed, from Task 3).
- Produces: `OpenAICompatibleProvider.__init__` gains a `langfuse_enabled: bool = False` keyword
  parameter; when true and no `client` is injected, constructs its OpenAI client via
  `from langfuse.openai import OpenAI` instead of `from openai import OpenAI` — a true drop-in
  swap, same class shape, zero changes to `complete()`/`complete_native_search()`.

- [ ] **Step 1: Write the failing test** — append to `tests/providers/test_openai_compatible.py`:

```python
def test_langfuse_enabled_flag_does_not_affect_behavior_when_client_given():
    captured: dict = {}
    provider = OpenAICompatibleProvider(
        name="openrouter", default_model="default-model", client=_FakeClient(captured),
        langfuse_enabled=True,
    )
    resp = provider.complete([ChatMessage(role="user", content="hi")])
    assert resp.content == "hello there"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/providers/test_openai_compatible.py -v`
Expected: FAIL — `TypeError: OpenAICompatibleProvider.__init__() got an unexpected keyword argument 'langfuse_enabled'`.

- [ ] **Step 3: Update `app/providers/llm/openai_compatible.py`**

```python
from app.providers.llm.base import ChatMessage, LLMResponse


class OpenAICompatibleProvider:
    """LLM provider for any OpenAI-compatible API (OpenRouter, NVIDIA, OpenAI)."""

    def __init__(
        self,
        *,
        name: str,
        default_model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        client=None,
        langfuse_enabled: bool = False,
    ) -> None:
        self.name = name
        self.default_model = default_model
        if client is not None:
            self._client = client
        elif langfuse_enabled:
            from langfuse.openai import OpenAI
            self._client = OpenAI(base_url=base_url, api_key=api_key)
        else:
            from openai import OpenAI
            self._client = OpenAI(base_url=base_url, api_key=api_key)

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        resp = self._client.chat.completions.create(
            model=model or self.default_model,
            messages=[m.model_dump() for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        choice = resp.choices[0]
        usage = resp.usage
        return LLMResponse(
            content=choice.message.content or "",
            model=resp.model,
            provider=self.name,
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
            finish_reason=choice.finish_reason,
        )

    def complete_native_search(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Native web search via OpenAI's Responses API (openai provider only).

        This is a different endpoint than complete()'s Chat Completions call --
        OpenAI's server-side web_search tool is only available on Responses.
        """
        if self.name != "openai":
            raise ValueError(
                f"Native web search via the Responses API is only supported for "
                f"provider 'openai', not {self.name!r}. Use RESEARCH_SEARCH_MODE=api instead."
            )
        instructions = "\n".join(m.content for m in messages if m.role == "system") or None
        input_messages = [
            {"role": m.role, "content": m.content} for m in messages if m.role != "system"
        ]
        resp = self._client.responses.create(
            model=model or self.default_model,
            instructions=instructions,
            input=input_messages,
            tools=[{"type": "web_search"}],
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        usage = resp.usage
        return LLMResponse(
            content=resp.output_text,
            model=resp.model,
            provider=self.name,
            prompt_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
            finish_reason=getattr(resp, "status", None),
        )
```

- [ ] **Step 4: Update `app/providers/llm/factory.py`** to pass the flag through — change the
  `OpenAICompatibleProvider(...)` construction:

```python
        return OpenAICompatibleProvider(
            name=provider,
            default_model=settings.llm_model,
            base_url=base_url,
            api_key=getattr(settings, key_attr),
            langfuse_enabled=settings.langfuse_enabled,
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/providers/test_openai_compatible.py -v`
Expected: PASS (5 passed — 4 existing + 1 new).

- [ ] **Step 6: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all green (166 total: 165 + 1 new).

- [ ] **Step 7: Report changes** to the user for review/commit.

---

### Task 6: `LeadOrchestratorAgent` tracing (nested spans)

**Files:**
- Modify: `app/agents/orchestrator_agent.py`
- Test: append to `tests/agents/test_orchestrator_agent.py`

**Interfaces:**
- Consumes: `traced_span`, `get_langfuse_client` (Task 3).
- Produces: `LeadOrchestratorAgent.__init__` gains an optional `langfuse_client=None` parameter
  (backward-compatible — every existing test constructs the agent without it, defaulting to
  `None`, so `traced_span` no-ops and behavior is unchanged). `run()` wraps itself in a
  `"lead-orchestrator-run"` span with nested `"research"`/`"qualify"`/`"draft"` child spans (draft
  only when the lead qualifies). `build_lead_orchestrator_agent(settings)` wires
  `get_langfuse_client(settings)` through.

- [ ] **Step 1: Write the failing test** — append to `tests/agents/test_orchestrator_agent.py`:

```python
class _FakeObservation:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeLangfuseClient:
    def __init__(self):
        self.calls: list[str] = []

    def start_as_current_observation(self, *, as_type, name):
        self.calls.append(name)
        return _FakeObservation()


def test_run_creates_traced_spans_when_langfuse_client_provided():
    scripts = [
        '{"score": 85, "reasoning": "Strong B2B fit."}',
        '{"subject": "Quick question", "body": "Hi -- noticed Acme makes widgets..."}',
    ]
    fake_client = _FakeLangfuseClient()
    agent = LeadOrchestratorAgent(
        _ScriptedLLM(scripts), _FakeResearchAgent(_brief()),
        icp_description="B2B companies", company_description="We sell tools for widget makers.",
        min_score_to_draft=60,
        langfuse_client=fake_client,
    )
    agent.run("acme.com")

    assert fake_client.calls == ["lead-orchestrator-run", "research", "qualify", "draft"]


def test_run_skips_draft_span_when_disqualified():
    scripts = ['{"score": 20, "reasoning": "Not a B2B fit."}']
    fake_client = _FakeLangfuseClient()
    agent = LeadOrchestratorAgent(
        _ScriptedLLM(scripts), _FakeResearchAgent(_brief()),
        icp_description="B2B companies", company_description="We sell tools for widget makers.",
        min_score_to_draft=60,
        langfuse_client=fake_client,
    )
    agent.run("acme.com")

    assert fake_client.calls == ["lead-orchestrator-run", "research", "qualify"]


def test_build_lead_orchestrator_agent_has_no_langfuse_client_when_disabled():
    s = Settings(
        _env_file=None, llm_provider="openrouter", llm_model="test-model",
        openrouter_api_key="k", research_search_mode="mock",
        icp_description="Test ICP", company_description="Test Offering",
        langfuse_enabled=False,
    )
    agent = build_lead_orchestrator_agent(s)
    assert agent._langfuse_client is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_orchestrator_agent.py -v`
Expected: FAIL — `TypeError: LeadOrchestratorAgent.__init__() got an unexpected keyword argument 'langfuse_client'`.

- [ ] **Step 3: Update `app/agents/orchestrator_agent.py`**

```python
from app.agents.structured import complete_structured
from app.observability.tracing import traced_span
from app.providers.llm.base import ChatMessage, LLMProvider
from app.schemas.lead import Lead, OutreachDraft, Qualification
from app.schemas.research import ResearchBrief

_QUALIFY_SYSTEM = """You are a lead qualification agent. Score how well a company fits an Ideal
Customer Profile (ICP), based on its research brief.

ICP:
{icp_description}

Respond with ONE JSON object and nothing else:
{{"score": <integer 0-100>, "reasoning": "..."}}

Rules:
- Score 0 = not a fit at all, 100 = a perfect fit.
- Base your reasoning only on the research brief provided -- do not invent facts.
- "score" and "reasoning" are both required."""

_DRAFT_SYSTEM = """You are a sales development representative writing a first-touch outreach
email on behalf of your own company (described below) to a prospective customer.

Your company:
{company_description}

Write a short, personalized message based on the prospect's research brief and why it qualifies
as a good fit, connecting a specific fact about the prospect to a specific, relevant benefit your
company offers.

Respond with ONE JSON object and nothing else:
{{"subject": "...", "body": "..."}}

Rules:
- Reference at least one specific fact from the prospect's research brief -- do not write a
  generic email.
- Reference at least one specific, relevant benefit or capability from your company's description
  above -- do not write generic sales language.
- Keep the body under 6 sentences.
- Do not invent facts not present in the research brief, the qualification reasoning, or your
  company's description.
- "subject" and "body" are both required."""


class LeadOrchestratorAgent:
    def __init__(
        self,
        llm: LLMProvider,
        research_agent,
        icp_description: str,
        company_description: str,
        min_score_to_draft: int = 60,
        langfuse_client=None,
    ) -> None:
        self._llm = llm
        self._research_agent = research_agent
        self._icp_description = icp_description
        self._company_description = company_description
        self._min_score_to_draft = min_score_to_draft
        self._langfuse_client = langfuse_client

    def run(self, target: str) -> Lead:
        with traced_span(self._langfuse_client, "lead-orchestrator-run"):
            with traced_span(self._langfuse_client, "research"):
                brief = self._research_agent.run(target)
            with traced_span(self._langfuse_client, "qualify"):
                qualification = self._qualify(brief)

            if qualification.score < self._min_score_to_draft:
                return Lead(
                    research=brief, qualification=qualification, outreach=None,
                    status="disqualified",
                )

            with traced_span(self._langfuse_client, "draft"):
                outreach = self._draft(brief, qualification)
            return Lead(
                research=brief, qualification=qualification, outreach=outreach,
                status="qualified",
            )

    def _qualify(self, brief: ResearchBrief) -> Qualification:
        messages = [
            ChatMessage(
                role="system",
                content=_QUALIFY_SYSTEM.format(icp_description=self._icp_description),
            ),
            ChatMessage(
                role="user", content=f"Research brief:\n{brief.model_dump_json(indent=2)}"
            ),
        ]
        result = complete_structured(self._llm, messages, Qualification)
        assert isinstance(result, Qualification)
        return result

    def _draft(self, brief: ResearchBrief, qualification: Qualification) -> OutreachDraft:
        messages = [
            ChatMessage(
                role="system",
                content=_DRAFT_SYSTEM.format(company_description=self._company_description),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"Research brief:\n{brief.model_dump_json(indent=2)}\n\n"
                    f"Why this company qualifies:\n{qualification.reasoning}"
                ),
            ),
        ]
        result = complete_structured(self._llm, messages, OutreachDraft)
        assert isinstance(result, OutreachDraft)
        return result


def build_lead_orchestrator_agent(settings) -> "LeadOrchestratorAgent":
    from app.agents.research_agent import build_research_agent
    from app.providers.llm.factory import build_llm_provider
    from app.providers.llm.fallback import FallbackLLM
    from app.observability.tracing import get_langfuse_client

    research_agent = build_research_agent(settings)
    llm = FallbackLLM(build_llm_provider(settings), settings.llm_fallback_model)
    return LeadOrchestratorAgent(
        llm=llm,
        research_agent=research_agent,
        icp_description=settings.icp_description,
        company_description=settings.company_description,
        min_score_to_draft=settings.icp_min_score_to_draft,
        langfuse_client=get_langfuse_client(settings),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_orchestrator_agent.py -v`
Expected: PASS (8 passed — 5 existing + 3 new).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all green (169 total: 166 + 3 new).

- [ ] **Step 6: Report changes** to the user for review/commit.

---

### Task 7: Learning guide + index updates

**Files:**
- Create: `docs/learning/phase-9-observability.md`
- Modify: `docs/learning/README.md`, `README.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Write `docs/learning/phase-9-observability.md`** — same structure as the Phase 1-8
  guides. Must cover:
  - **What & why** — `Settings` carried unused `langfuse_*` fields since Phase 1; why this phase
    finally wires them up; why Prometheus metrics use a dedicated `CollectorRegistry` (avoids
    duplicate-timeseries errors across test files that import the module repeatedly); why
    `/metrics` stays unauthenticated (matches `/health`/`/ready` precedent, standard Prometheus
    scrape practice); why one trace per `LeadOrchestratorAgent.run()` (the natural "one company
    researched" unit).
  - **The flow** — `setup_observability(settings) -> configure_logging() + (Anthropic
    instrumentation if enabled) + get_langfuse_client(settings)`, called once from `create_app()`
    and every CLI script; then per-request: request-ID middleware -> metrics middleware -> route
    handler; per orchestrator run: `traced_span("lead-orchestrator-run")` wrapping nested
    research/qualify/draft spans, with LLM generations auto-captured by the provider-level
    instrumentation.
  - **File-by-file walkthrough** — `app/observability/logging_config.py` (JSON formatter +
    `contextvars`-based request ID, why `contextvars` instead of threading an ID through every
    function signature); `app/observability/metrics.py` (the three metrics, why a dedicated
    registry); `app/observability/tracing.py` (the `Settings` -> `os.environ` bridge, why both
    `LANGFUSE_HOST` and `LANGFUSE_BASE_URL` get set, the `traced_span` no-op-when-disabled
    pattern); `app/observability/setup.py` (the single entry point, `@lru_cache` for
    "instrument Anthropic exactly once"); `app/providers/llm/openai_compatible.py` (the drop-in
    `langfuse.openai` import swap); `app/agents/orchestrator_agent.py` (the nested span
    structure).
  - **Key concepts table** — no-op-by-default cross-cutting instrumentation (never touches
    real credentials in tests), `contextvars` for implicit per-request context, dedicated metrics
    registries for test isolation, drop-in SDK wrappers vs. global auto-instrumentation as two
    different integration shapes for the same underlying goal.
  - **How to run & test** — `pytest tests/observability tests/api/test_jobs.py
    tests/providers/test_openai_compatible.py tests/agents/test_orchestrator_agent.py -v`; how to
    actually enable Langfuse (`LANGFUSE_ENABLED=true` + real keys in `.env`) and see a trace;
    `curl http://localhost:8000/metrics` to see real Prometheus output.
  - **What's next** — Phase 10: n8n integration (ingestion, human-approval sending, alerting) —
    alerting there will likely want to react to the job-outcome metrics/logs this phase produces.

- [ ] **Step 2: Update `docs/learning/README.md`** — add a row to the phase-guides table:

```markdown
| [Phase 9 — Observability](phase-9-observability.md) | Langfuse tracing (one trace per lead run, across all 4 LLM providers), Prometheus metrics (`/metrics`: requests + job outcomes), and structured JSON logging with request IDs — all through one `setup_observability()` entry point shared by the API and CLI scripts. |
```

And update the mental-model diagram's Phase 9 line:

```
Phase 9  Observability ....... Langfuse tracing, Prometheus metrics, structured JSON logging
```

- [ ] **Step 3: Update `README.md`** — change the Phase 9 status line and the "Current" marker:

```markdown
- [x] Phase 9 — Observability (Langfuse tracing across all 4 providers; Prometheus `/metrics`; structured JSON logging)
```

- [ ] **Step 4: Report changes** to the user for review/commit.

---

## Phase 9 Definition of Done

- `./.venv/Scripts/python.exe -m pytest -q` → all green (Phase 1-9), no network or real Langfuse
  credentials required anywhere.
- `GET /metrics` returns real Prometheus text format with request and job-outcome metrics present.
- Structured JSON logs appear on stdout for both the API and CLI scripts, each request correlated
  with a request ID.
- With `LANGFUSE_ENABLED=true` and real keys set, a real `scripts/try_lead.py` run produces one
  trace in the Langfuse UI named "lead-orchestrator-run" with nested research/qualify/draft spans
  and LLM generations underneath.
- Learning guide written; README + learning index updated.

**Next phase (planned just-in-time after this one):** Phase 10 — n8n integration (ingestion,
human-approval sending, alerting).
