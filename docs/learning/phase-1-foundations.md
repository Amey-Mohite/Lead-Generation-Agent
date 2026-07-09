# Phase 1 — Foundations (Learning Guide)

> **Goal of this phase:** stand up a *runnable, testable, deployable* web service skeleton.
> No agent logic yet — just the reliable "body" that every later feature plugs into.

---

## 1. What & why

Before you can build anything clever, you need a service that:

- **Reads its configuration from the environment** (not hard-coded) — so the same code runs
  on your laptop, in Docker, and in Kubernetes with different settings and *no code changes*.
- **Answers "am I alive?" and "am I ready?"** — so an orchestrator (Docker, Kubernetes) can
  supervise it: restart it when it hangs, stop sending it traffic when its database is down.
- **Talks to a database** through a single, shared connection.
- **Packages into a container** that runs the same everywhere.

If you skip this and jump straight to agent code, you end up with something that "works on my
machine" and falls apart the moment you try to deploy or debug it. Foundations first.

---

## 2. The flow

What happens when a request hits the service:

```
                 ┌────────────────────────────────────────────┐
   HTTP request  │                FastAPI app                  │
  ───────────────►  (built by create_app() in app/main.py)     │
                 │                                              │
                 │   GET /health ──► returns {"status":"ok"}    │  ← liveness (no deps)
                 │                                              │
                 │   GET /ready  ──► get_engine() ─► SELECT 1   │  ← readiness (checks DB)
                 │                        │                     │
                 └────────────────────────┼─────────────────────┘
                                          │
                                   ┌──────▼───────┐
                                   │  PostgreSQL   │
                                   └──────────────┘

  Configuration (app/config.py) is read ONCE from ENV / .env at startup
  and injected wherever it's needed.
```

**The key idea:** configuration flows *in* from the environment; health/readiness flow *out*
to whoever is supervising the process.

---

## 3. File-by-file walkthrough

### `app/config.py` — the single source of truth
```python
class Settings(BaseSettings):
    llm_provider: str = "openrouter"
    database_url: str = "postgresql+psycopg://..."
    # ... every knob in the system
```
- **What:** a typed `Settings` object. Every field auto-populates from an environment variable
  (`LLM_PROVIDER`, `DATABASE_URL`, …) or falls back to its default.
- **Why typed:** if `RATE_LIMIT_PER_MIN` should be an int, pydantic converts and validates it —
  you catch bad config at startup, not at 2 a.m. in production.
- **Why `get_settings()` + `@lru_cache`:** the config is parsed **once** and the same instance is
  reused everywhere. FastAPI injects it as a dependency (see `/health`). Caching also means tests
  can call `get_settings.cache_clear()` to reset between cases.
- **The payoff:** this is the literal mechanism behind "swap providers via ENV." Changing
  behavior = changing an env var, never editing code.

> **When to use this pattern:** *always*, for any service. It's the "12-factor config" principle —
> config lives in the environment, code stays identical across environments.

### `app/main.py` — the app factory
```python
def create_app() -> FastAPI:
    app = FastAPI(...)
    app.include_router(health_router)
    return app

app = create_app()
```
- **What:** a *function* that builds and returns the app, plus a module-level `app` for uvicorn.
- **Why a factory (not just a global):** tests need **fresh, isolated** app instances. In Phase 1's
  `/ready` tests you'll see each test build its own app with a different (fake) database — only
  possible because `create_app()` can be called repeatedly.

> **When to use:** any time you have setup that tests need to vary (DB, config, plugins). Factories
> keep global state out of your tests.

### `app/api/health.py` — liveness & readiness
```python
@router.get("/health")   # liveness — no dependencies
@router.get("/ready")    # readiness — pings the DB, returns 503 if down
```
- **The distinction that matters in production:**
  - `/health` (**liveness**) answers "is the process alive?" It has **no dependencies** on purpose.
    If it fails, the orchestrator **restarts** the container.
  - `/ready` (**readiness**) answers "can I serve traffic *right now*?" It runs `SELECT 1`. If the
    DB is down it returns **503**, and the orchestrator **stops routing traffic** but does *not*
    kill the pod (the DB might recover; killing wouldn't help).
- **Why this split is not pedantic:** if you used one combined check, a brief DB blip would cause
  Kubernetes to kill and restart healthy app pods in a loop — an outage you caused yourself.

> **When to use:** every containerized service should expose both. It's what makes rolling
> deploys and self-healing work.

### `app/db/session.py` — one shared engine
```python
@lru_cache
def get_engine() -> Engine:
    return create_engine(settings.database_url, pool_pre_ping=True)
```
- **What:** a cached SQLAlchemy `Engine` (a connection pool), built once.
- **Why `pool_pre_ping=True`:** before handing out a pooled connection, SQLAlchemy sends a tiny
  "are you still there?" ping. Without it, a connection that died while idle (DB restart, network
  blip) throws a confusing error on first use. This trades a microscopic latency for robustness.
- **Why cached:** you want *one* pool for the whole process, not a new one per request.

### `Dockerfile` — reproducible packaging
- Slim Python base, installs deps, **creates and switches to a non-root `appuser`.**
- **Why non-root:** if the container is ever compromised, the attacker isn't root inside it. This is
  a baseline security expectation and something clients specifically look for.
- **`HEALTHCHECK`** tells Docker how to call `/health` so `docker ps` shows real health.

### `deploy/docker-compose.yml` — the local stack
- Two services: `app` and `db` (Postgres).
- **`depends_on: condition: service_healthy`** + a Postgres `healthcheck` means the app container
  waits until Postgres is *actually accepting connections* — not just "started." This avoids the
  classic race where the app boots faster than the DB and crashes on first query.

---

## 4. Key concepts (transferable)

| Concept | In one line | When to reach for it |
|---------|-------------|----------------------|
| 12-factor config | Config in ENV, code identical everywhere | Every service, always |
| App factory | A function that builds the app | When tests must vary setup |
| Liveness vs readiness | "alive?" vs "ready to serve?" | Any containerized/k8s service |
| Connection pool + pre-ping | Reuse DB connections, verify before use | Any DB-backed service |
| Non-root container | Drop privileges inside the image | Every production container |
| Healthcheck + depends_on | Start services in the right order | Multi-service compose/k8s |

---

## 5. How to run & test it

```bash
# Unit tests (no Docker, no DB needed — /ready is tested with fake engines)
./.venv/Scripts/python.exe -m pytest tests/test_config.py tests/test_health.py tests/test_ready.py -v

# Full local stack (needs Docker running)
docker compose -f deploy/docker-compose.yml up --build
curl http://localhost:8000/health   # {"status":"ok","version":"0.1.0"}
curl http://localhost:8000/ready    # {"status":"ready","database":"up"}
docker compose -f deploy/docker-compose.yml down
```

> **Note:** the live Docker stack verification is currently *deferred* pending a working local
> Docker daemon. The unit tests fully cover config, `/health`, and both `/ready` paths (DB up and
> DB down) using fake engines — so the logic is proven without Docker.

### How the tests prove the behavior
- `test_config.py` — defaults load; an env var overrides a field; the settings object is cached.
- `test_health.py` — `/health` returns 200 with a version.
- `test_ready.py` — **monkeypatches** `get_engine` with a fake "up" engine (expects 200) and a fake
  "down" engine that raises (expects 503). This is *dependency injection for testing*: we test the
  failure path without needing a broken database.

---

## 6. What's next

Phase 1 gives us the body. **Phase 2** gives it a voice — the ability to talk to any LLM provider
through one uniform interface, selected by config. See
[phase-2-llm-provider-layer.md](phase-2-llm-provider-layer.md).
