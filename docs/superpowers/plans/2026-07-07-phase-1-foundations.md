# Phase 1: Foundations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a runnable, testable FastAPI service skeleton with ENV-driven config, a Postgres connection, health/readiness endpoints, containerization, and portfolio scaffolding — the foundation every later phase builds on.

**Architecture:** A `create_app()` FastAPI factory wires routers. Configuration is centralized in a `pydantic-settings` `Settings` object read from ENV/`.env`. Database access goes through a single cached SQLAlchemy `Engine`. `/health` is a pure liveness check; `/ready` pings the DB for Kubernetes readiness. Everything runs locally via `docker-compose` (app + Postgres).

**Tech Stack:** Python 3.12, FastAPI, uvicorn, pydantic-settings, SQLAlchemy 2.0, psycopg 3, pytest + httpx, ruff, mypy, Docker/Compose, Postgres 16.

## Global Constraints

- **Python:** 3.12+
- **Package layout:** source under `app/`, tests under `tests/`, import root is `app`.
- **Config:** all runtime config via `pydantic-settings` reading ENV/`.env`; **no secrets in code or git**. `.env` is git-ignored; `.env.example` documents every variable with empty/placeholder values.
- **Providers/back-ends via ENV:** switching LLM provider, search mode, or exporters is a config change, never a code change (seams enforced in later phases; config keys defined here).
- **Search default:** `RESEARCH_SEARCH_MODE=native`. **LLM default:** `LLM_PROVIDER=openrouter` (must be trivially switchable).
- **Container:** slim base image, runs as **non-root**.
- **Every task ends** with: tests green, README updated where relevant, one commit.
- **License:** MIT.

---

### Task 1: Project skeleton + config module

**Files:**
- Create: `pyproject.toml`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `.env.example`
- Create: `LICENSE`
- Create: `tests/__init__.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `app.config.Settings` (pydantic-settings model); `app.config.get_settings() -> Settings` (lru_cached). Key fields: `app_name: str`, `app_version: str`, `environment: str`, `llm_provider: str`, `llm_model: str`, `research_search_mode: str`, `exporters: str`, `export_dir: str`, `langfuse_enabled: bool`, `database_url: str`, `api_key: str | None`, `rate_limit_per_min: int`.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "lead-gen-agent"
version = "0.1.0"
description = "Autonomous multi-agent lead-generation system"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "pydantic-settings>=2.4",
    "sqlalchemy>=2.0",
    "psycopg[binary]>=3.2",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "httpx>=0.27",
    "ruff>=0.6",
    "mypy>=1.11",
]

[tool.hatch.build.targets.wheel]
packages = ["app"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

- [ ] **Step 2: Create package + test init files**

Create empty `app/__init__.py` containing:
```python
__version__ = "0.1.0"
```
Create empty `tests/__init__.py` (0 bytes).

- [ ] **Step 3: Write the failing test** — `tests/test_config.py`

```python
from app.config import Settings, get_settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.app_name == "lead-gen-agent"
    assert s.llm_provider == "openrouter"
    assert s.research_search_mode == "native"
    assert s.exporters == "excel"
    assert s.langfuse_enabled is False


def test_env_override(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("RESEARCH_SEARCH_MODE", "mock")
    s = Settings(_env_file=None)
    assert s.llm_provider == "anthropic"
    assert s.research_search_mode == "mock"


def test_get_settings_is_cached():
    get_settings.cache_clear()
    assert get_settings() is get_settings()
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.config'`.

- [ ] **Step 5: Create `app/config.py`**

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # App
    app_name: str = "lead-gen-agent"
    app_version: str = "0.1.0"
    environment: str = "development"

    # LLM (swap provider with one line)
    llm_provider: str = "openrouter"          # openrouter|nvidia|openai|anthropic|local
    llm_model: str = "anthropic/claude-sonnet-5"
    llm_fallback_model: str | None = None
    openrouter_api_key: str | None = None
    nvidia_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # Research / search (default: native = model does its own web search)
    research_search_mode: str = "native"      # native|api|mock
    search_provider: str = "tavily"           # tavily|serpapi|brave
    search_api_key: str | None = None

    # Outputs
    exporters: str = "excel"                  # comma list: excel,slack,email,gmail
    export_dir: str = "./out/leads"
    slack_webhook_url: str | None = None
    smtp_url: str | None = None
    gmail_credentials: str | None = None

    # Observability
    langfuse_enabled: bool = False
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None

    # Infra / API
    database_url: str = "postgresql+psycopg://leads:leads@localhost:5432/leads"
    api_key: str | None = None
    rate_limit_per_min: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Create `.env.example`**

```env
# App
ENVIRONMENT=development

# LLM (swap provider with one line)
LLM_PROVIDER=openrouter
LLM_MODEL=anthropic/claude-sonnet-5
LLM_FALLBACK_MODEL=
OPENROUTER_API_KEY=
NVIDIA_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=

# Research / search
RESEARCH_SEARCH_MODE=native
SEARCH_PROVIDER=tavily
SEARCH_API_KEY=

# Outputs
EXPORTERS=excel
EXPORT_DIR=./out/leads
SLACK_WEBHOOK_URL=
SMTP_URL=
GMAIL_CREDENTIALS=

# Observability
LANGFUSE_ENABLED=false
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=

# Infra / API
DATABASE_URL=postgresql+psycopg://leads:leads@localhost:5432/leads
API_KEY=
RATE_LIMIT_PER_MIN=60
```

- [ ] **Step 8: Create `LICENSE`** — standard MIT license text, copyright `2026 Amey Mohite`.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml app/ tests/ .env.example LICENSE
git commit -m "feat: project skeleton and pydantic-settings config"
```

---

### Task 2: FastAPI app factory + `/health` endpoint

**Files:**
- Create: `app/main.py`
- Create: `app/api/__init__.py`
- Create: `app/api/health.py`
- Test: `tests/test_health.py`

**Interfaces:**
- Consumes: `app.config.Settings`, `app.config.get_settings`.
- Produces: `app.main.create_app() -> FastAPI`; `app.main.app` (module-level instance); `app.api.health.router` (APIRouter). `GET /health` → `200 {"status": "ok", "version": <app_version>}`.

- [ ] **Step 1: Write the failing test** — `tests/test_health.py`

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_health_ok():
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`.

- [ ] **Step 3: Create `app/api/__init__.py`** (empty, 0 bytes).

- [ ] **Step 4: Create `app/api/health.py`**

```python
from fastapi import APIRouter, Depends

from app.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    return {"status": "ok", "version": settings.app_version}
```

- [ ] **Step 5: Create `app/main.py`**

```python
from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.include_router(health_router)
    return app


app = create_app()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_health.py -v`
Expected: PASS (1 passed).

- [ ] **Step 7: Manual smoke check**

Run: `python -m uvicorn app.main:app --port 8000` then in another shell `curl http://localhost:8000/health`
Expected: `{"status":"ok","version":"0.1.0"}`. Stop the server.

- [ ] **Step 8: Commit**

```bash
git add app/main.py app/api/ tests/test_health.py
git commit -m "feat: FastAPI app factory and /health endpoint"
```

---

### Task 3: Database engine + `/ready` readiness probe

**Files:**
- Create: `app/db/__init__.py`
- Create: `app/db/session.py`
- Modify: `app/api/health.py` (add `/ready`)
- Test: `tests/test_ready.py`

**Interfaces:**
- Consumes: `app.config.get_settings`.
- Produces: `app.db.session.get_engine() -> sqlalchemy.Engine` (lru_cached, `pool_pre_ping=True`). `GET /ready` → `200 {"status":"ready","database":"up"}` when a `SELECT 1` succeeds, else `503 {"status":"not_ready","database":"down"}`.

- [ ] **Step 1: Write the failing test** — `tests/test_ready.py`

```python
from fastapi.testclient import TestClient

import app.api.health as health_module
from app.main import create_app


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *_a, **_k):
        return None


class _UpEngine:
    def connect(self):
        return _FakeConn()


class _DownEngine:
    def connect(self):
        raise RuntimeError("db down")


def test_ready_up(monkeypatch):
    monkeypatch.setattr(health_module, "get_engine", lambda: _UpEngine())
    client = TestClient(create_app())
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["database"] == "up"


def test_ready_down(monkeypatch):
    monkeypatch.setattr(health_module, "get_engine", lambda: _DownEngine())
    client = TestClient(create_app())
    resp = client.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["database"] == "down"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ready.py -v`
Expected: FAIL (`/ready` returns 404 / `get_engine` missing).

- [ ] **Step 3: Create `app/db/__init__.py`** (empty, 0 bytes).

- [ ] **Step 4: Create `app/db/session.py`**

```python
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True)
```

- [ ] **Step 5: Add `/ready` to `app/api/health.py`**

Replace the file contents with:
```python
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import Settings, get_settings
from app.db.session import get_engine

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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_ready.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -v`
Expected: PASS (6 passed).

- [ ] **Step 8: Commit**

```bash
git add app/db/ app/api/health.py tests/test_ready.py
git commit -m "feat: DB engine and /ready readiness probe"
```

---

### Task 4: Containerization (Dockerfile + docker-compose)

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `deploy/docker-compose.yml`

**Interfaces:**
- Consumes: `app.main:app`, `.env`.
- Produces: a runnable stack — `app` (port 8000) + `db` (Postgres 16, port 5432) — where `/health` and `/ready` both return healthy.

- [ ] **Step 1: Create `Dockerfile`**

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir . \
    && useradd --create-home appuser

USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Create `.dockerignore`**

```
.venv/
venv/
__pycache__/
*.pyc
tests/
docs/
out/
.env
.git/
```

- [ ] **Step 3: Create `deploy/docker-compose.yml`**

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

volumes:
  pgdata:
```

- [ ] **Step 4: Build and run the stack**

Run: `docker compose -f deploy/docker-compose.yml up --build -d`
Expected: both containers start; `db` becomes healthy.

- [ ] **Step 5: Verify endpoints against the running stack**

Run: `curl http://localhost:8000/health` then `curl -i http://localhost:8000/ready`
Expected: `/health` → `{"status":"ok",...}`; `/ready` → HTTP 200 `{"status":"ready","database":"up"}`.

- [ ] **Step 6: Tear down**

Run: `docker compose -f deploy/docker-compose.yml down`

- [ ] **Step 7: Commit**

```bash
git add Dockerfile .dockerignore deploy/docker-compose.yml
git commit -m "feat: Dockerfile and docker-compose (app + postgres)"
```

---

### Task 5: Portfolio README skeleton + run docs

**Files:**
- Create/Modify: `README.md`

**Interfaces:**
- Produces: the living, portfolio-grade README that every later phase appends to.

- [ ] **Step 1: Create `README.md`**

````markdown
# 🤖 Autonomous Lead-Generation Agent

Production-grade, multi-model AI agent that researches a company, qualifies it against
an Ideal Customer Profile, and drafts personalized outreach — exposed as an API, with
n8n human-approval sending and a React dashboard.

> Portfolio project demonstrating: multi-agent design · tool-calling · multi-provider
> LLM routing (OpenRouter / NVIDIA / OpenAI / Anthropic, swap via ENV) · Postgres
> observability · FastAPI · Langfuse tracing · Docker → Kubernetes (minikube).

## Status

Built in phases (see `docs/superpowers/plans/`). **Current: Phase 1 — Foundations** ✅

- [x] Phase 1 — Foundations: config, FastAPI, Postgres, health/ready, Docker
- [ ] Phase 2 — Multi-provider LLM layer
- [ ] Phase 3 — Research sub-agent + tools
- [ ] Phase 4 — Orchestrator agent (qualify + draft)
- [ ] Phases 5–12 — persistence, API, exporters, dashboard, observability, n8n, deploy

## Architecture

See the design spec: `docs/superpowers/specs/2026-07-07-lead-generation-agent-design.md`.

## Quickstart

```bash
cp .env.example .env          # fill in keys as needed (works with defaults for Phase 1)
docker compose -f deploy/docker-compose.yml up --build
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

### Local dev (without Docker)

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows
pip install -e ".[dev]"
python -m pytest -v
python -m uvicorn app.main:app --reload
```

## Configuration

All configuration is via ENV / `.env` (see `.env.example`). Switching LLM provider,
search mode, or exporters is a one-line config change — never a code change.

## What I can build for you

Custom AI agents, multi-agent systems, n8n automations, multi-model LLM integrations,
and Kubernetes-deployable services. This repo is a working reference of that stack.

## License

MIT — see [LICENSE](LICENSE).
````

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: portfolio README skeleton with quickstart"
```

---

## Phase 1 Definition of Done

- `python -m pytest -v` → all green (config, health, ready).
- `docker compose -f deploy/docker-compose.yml up --build` → `/health` and `/ready` both healthy.
- `.env.example` documents every config key; no secrets committed.
- README renders with quickstart + phase checklist.
- Five commits telling the story: config → app/health → db/ready → docker → readme.

**Next phase (planned just-in-time after this one):** Phase 2 — the multi-provider LLM
abstraction (`LLMProvider` interface, OpenRouter + NVIDIA adapters, factory, fallback router).
