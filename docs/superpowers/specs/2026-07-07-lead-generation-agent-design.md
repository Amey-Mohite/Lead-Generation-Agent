# Design Spec — Autonomous Multi-Agent Lead-Generation System

**Date:** 2026-07-07
**Status:** Approved design (pre-implementation)
**Author:** Amey Mohite (with Claude)

---

## 1. Purpose & Context

A **production-grade, portfolio-ready** autonomous lead-generation agent, built as a
learning project and published on Upwork to win AI-agent / automation contracts.

The system takes a target (a company domain, or Ideal Customer Profile criteria),
**autonomously researches** it, **qualifies** it against an ICP, **drafts personalized
outreach**, and delivers the result through configurable channels (Excel now; Slack /
Email / Gmail as options), with a human-approval gate before any outreach is sent.

Two goals carry equal weight:
1. **Learn agent building** from architecture up — multi-agent loops, tool use,
   provider abstraction, observability, deployment.
2. **Sell** — a polished, demoable, credible portfolio piece that maps to the exact
   phrases clients search for ("AI agent", "multi-agent", "n8n automation", "multi-model",
   "Kubernetes deploy").

### Working mode (important)
This is a **teaching build**, executed **one phase at a time**:
- Explain the *why* (pattern, trade-off, production reasoning) alongside the code.
- **README is a living document** — updated after every phase; doubles as the Upwork narrative.
- Small, understandable commits per phase that tell a story.

---

## 2. Scope

### In scope (v1)
- Multi-agent system: **Research Sub-Agent** + **Lead Orchestrator Agent**.
- **Autonomous** self-directed research with a **search-mode flag** (`api | native | mock`).
- **Multi-provider LLM abstraction**, swappable via ENV: OpenRouter, NVIDIA, OpenAI,
  Anthropic, local.
- **Structured, validated Lead output** (Pydantic schema: score, reasoning, contacts, draft).
- **Pluggable Exporters/Notifiers**: `excel` (default, writes `.xlsx` to a folder),
  `slack`, `email`, `gmail`.
- **FastAPI** REST API (API-key auth, rate limiting, OpenAPI docs) — "share as an API".
- **Minimal web dashboard** — enter a domain, watch research live, see scored lead + draft.
- **Postgres** logging & storage (leads, contacts, runs, request logs, enrichment cache).
- **Langfuse** LLM/agent tracing (config-toggleable).
- **n8n** orchestration: trigger → run agent → human-approval → send outreach.
- **Deployment**: `docker-compose` (dev) → **minikube** (Kubernetes manifests/Helm).
- **Quality**: unit + integration tests, eval harness, CI (GitHub Actions).
- **Portfolio polish**: README, architecture diagram, demo GIF/screenshots, MIT license,
  `.env.example`, one-command quickstart.

### Out of scope (v1 — noted as "future")
- RAG / vector store (may return later for lead dedup/memory).
- Multi-tenant auth/billing; user accounts.
- Voice; model fine-tuning.
- A generic plugin/skill framework (single-purpose by choice).
- Production-scale scraping infra (proxies, CAPTCHA solving).

---

## 3. Architecture Overview

```
        POST /v1/leads {domain / ICP}
                │
        ┌───────▼───────────────────────────────────┐
        │          Lead Orchestrator Agent            │
        │   plans pipeline, delegates, qualifies,     │
        │   drafts, decides                           │
        └───┬───────────────┬──────────────┬──────────┘
            │ delegate       │ qualify       │ draft
     ┌──────▼───────┐   (LLM call)      (LLM call)
     │ Research     │        │              │
     │ Sub-Agent    │        ▼              ▼
     │ (tool loop)  │   Lead score +   Personalized
     │              │   reasoning      outreach draft
     │ tools:       │        │              │
     │ • web_search │        └──────┬───────┘
     │ • fetch_url  │               ▼
     └──────┬───────┘        Postgres (lead + run trace)
            │                        │
   ResearchBrief            ┌────────┴─────────┐
   (structured, cited)      ▼                  ▼
                        Exporters          n8n workflow
                        excel/slack/       → human approve
                        email/gmail        → send outreach
```

**Core principle — program to interfaces, select by config.** The agents never touch a
vendor SDK directly. They depend on abstractions; concrete implementations are chosen at
runtime from ENV. The seams that must exist from day one:

| Interface | Purpose | Implementations |
|-----------|---------|-----------------|
| `LLMProvider` | text/chat + tool-calling | openrouter, nvidia, openai, anthropic, local |
| `SearchBackend` | the `web_search` tool | tavily/serpapi/brave (api), native, mock |
| `Tool` | agent-callable tools | `web_search`, `fetch_url` (extensible) |
| `Exporter` | deliver leads | excel, slack, email, gmail |
| `Store` (repos) | persistence | Postgres (SQLAlchemy) |

---

## 4. The Two Agents

### 4.1 Research Sub-Agent (autonomous tool loop)
- **Input:** a target (domain / company name / ICP hint).
- **Behavior:** a ReAct-style loop — it *decides* what to search, issues queries via the
  `web_search` tool, reads results, `fetch_url`s promising pages, follows up, and stops
  when it has enough evidence.
- **Search mode flag** (`RESEARCH_SEARCH_MODE`, default `native`):
  - `native` (**default**) — web-first: uses a web-search-enabled model's built-in search
    (e.g. Perplexity/`:online` models via OpenRouter). Fewest moving parts, no extra key.
  - `api` — calls an external search API (`SEARCH_PROVIDER`: tavily/serpapi/brave) for
    finer control / provider-independence.
  - `mock` — offline canned results so demos run with zero keys.
- **Output:** a structured, cited **`ResearchBrief`** (company facts, industry, size,
  tech signals, contacts, sources).
- **This is the core agent-building lesson:** plan → tool → observe → repeat → finish.

### 4.2 Lead Orchestrator Agent (the manager)
- Calls the Research Sub-Agent.
- **Qualifies** the lead against a configurable **ICP** — produces a numeric score +
  written reasoning.
- **Drafts** a personalized outreach message grounded in the research.
- Emits a validated **`Lead`** record; persists it; hands to Exporters / n8n.

---

## 5. Data Model (Postgres)

| Table | Purpose |
|-------|---------|
| `leads` | company, domain, industry, size, score, reasoning, status |
| `contacts` | people found (name, role, email, source), FK → lead |
| `research_briefs` | structured research output + sources, FK → lead |
| `outreach_drafts` | draft message, channel, approval status, FK → lead |
| `agent_runs` | per-run **trace**: agent, steps, tool calls, decisions, status |
| `request_logs` | per-request: provider, model, tokens in/out, cost est., latency, status |
| `enrichment_cache` | cached search/fetch results (avoid repeat cost) |

`agent_runs` + `request_logs` are the **observability backbone** and the source for
dashboards that impress clients. Migrations via **Alembic**.

---

## 6. API Surface (FastAPI)

- `POST /v1/leads` — submit a target; run the pipeline (sync or background job).
- `GET  /v1/leads/{id}` — fetch a lead + brief + draft.
- `GET  /v1/leads` — list / filter leads.
- `POST /v1/leads/{id}/export` — push to configured exporters.
- `POST /v1/ingest` — (reserved) accept targets in bulk (used by n8n).
- `GET  /health`, `GET /ready` — k8s liveness/readiness probes.
- `GET  /metrics` — Prometheus metrics.
- Cross-cutting: API-key auth (header), per-key rate limiting, request-id middleware,
  structured JSON logs, auto OpenAPI/Swagger at `/docs`.

---

## 7. Configuration (12-factor, ENV-driven)

The whole "swap via ENV" story on one screen (via `pydantic-settings`, with `.env.example`):

```env
# LLM
LLM_PROVIDER=openrouter          # openrouter|nvidia|openai|anthropic|local
LLM_MODEL=anthropic/claude-sonnet-5
LLM_FALLBACK_MODEL=meta-llama/llama-3.3-70b
OPENROUTER_API_KEY=
NVIDIA_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=

# Research / search  (default: native = the model does its own web search)
RESEARCH_SEARCH_MODE=native      # native|api|mock
SEARCH_PROVIDER=tavily           # used when mode=api: tavily|serpapi|brave
SEARCH_API_KEY=

# Outputs
EXPORTERS=excel                  # comma list: excel,slack,email,gmail
EXPORT_DIR=./out/leads
SLACK_WEBHOOK_URL=
SMTP_URL=
GMAIL_CREDENTIALS=

# Observability
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=

# Infra / API
DATABASE_URL=postgresql+psycopg://user:pass@db:5432/leads
API_KEY=
RATE_LIMIT_PER_MIN=60
```

---

## 8. Providers, Reliability & Guardrails

- **Provider factory** reads config and returns the right `LLMProvider` /
  `SearchBackend` / `Exporter`. Adding OpenAI/Claude later = one adapter file + one ENV line.
- **Fallback router**: primary model error/rate-limit → automatic retry on
  `LLM_FALLBACK_MODEL` (production feature + demo talking point).
- **Reliability**: request timeouts, retries with exponential backoff, circuit-breaker-lite,
  enrichment caching.
- **Guardrails**: input validation, prompt-injection heuristics, tool-call allow-listing,
  spend/step caps per run (prevent runaway loops), "human approval required" before send.

---

## 9. Observability

- **Langfuse** — traces every agent step and tool call across both agents; token/cost/latency;
  screenshot-ready UI. Behind a thin `tracing` wrapper, toggled by `LANGFUSE_ENABLED`.
- **Structured JSON logging** with request IDs.
- **Prometheus `/metrics`** — request counts, latencies, error rates, tokens, run outcomes.

---

## 10. n8n Orchestration (on minikube)

1. **Trigger workflow** — webhook / Google-Sheet / CRM row → `POST /v1/leads`.
2. **Approval + send workflow** — reads `outreach_drafts` (status=pending) → routes to a
   human (Slack/email) → on approve → **sends** via Gmail/email/Slack → writes status back.
3. **Alerting workflow** — on error-rate / health failure → notify.

App produces leads + Excel + events; **n8n owns the human-approval gate and actual sending**.
The in-app Exporters provide the direct path; n8n provides the orchestrated path.

---

## 11. Deployment

- **Dev:** `docker-compose` — app + Postgres + n8n + (optional) Langfuse.
- **Prod target:** **minikube** / Kubernetes — Deployments (app, n8n), StatefulSet+PVC
  (Postgres), Services, ConfigMaps/Secrets, health probes, Ingress. Optional small Helm chart.
- **Migrations:** Alembic run as an init job.
- **Container:** non-root, slim image, healthcheck.

---

## 12. Testing & Quality

- **Unit tests** — providers/exporters mocked; agent decision logic.
- **Integration tests** — Postgres via docker-compose/testcontainers; end-to-end pipeline in `mock` mode.
- **Eval harness** — small fixture set of targets scored for research quality + draft quality.
- **CI (GitHub Actions)** — ruff (lint), mypy (types), pytest, docker build.

---

## 13. Repository Structure

```
app/
  api/            # FastAPI routes, middleware, deps, auth
  agents/         # orchestrator, research_agent, prompts, guardrails
  providers/      # llm/, search/, exporters/ + factory
  tools/          # web_search, fetch_url (Tool interface + registry)
  schemas/        # Pydantic: Lead, Contact, ResearchBrief, OutreachDraft
  db/             # models, repositories, session, migrations (alembic)
  observability/  # logging, metrics, langfuse tracing
  config.py       # pydantic-settings
  main.py
ui/               # React dashboard (Vite) — its own build, calls the API
n8n/              # exported workflow JSON
deploy/           # docker-compose, k8s manifests / helm
tests/
docs/
README.md         # living, portfolio-grade
```

---

## 14. Portfolio-Readiness (cross-cutting)

- **README**: hero blurb, architecture diagram, demo GIF/screenshots, feature list,
  tech-stack badges, "How it works", one-command `docker-compose up` quickstart,
  "What I can build for you" section.
- **Hygiene**: MIT `LICENSE`, `.gitignore`, `.env.example` (zero real secrets — critical),
  clean per-phase commit history.
- **Demo assets**: architecture image + recorded GIF (domain → research → scored lead + draft).
- **Safety/cost notes**: `mock` mode runs free; scraping/outreach ToS disclaimers.

---

## 15. Build Phases (step-by-step)

Each phase ends with: tests green, README updated, a commit, and a short explainer.

1. **Foundations** — repo, config (`pydantic-settings`), Docker, Postgres, `/health`, README skeleton, LICENSE, `.env.example`.
2. **Provider layer** — `LLMProvider` interface + OpenRouter & NVIDIA (+ OpenAI/Anthropic stubs), factory, fallback router.
3. **Tools + Research Sub-Agent** — `Tool` interface, `web_search` (api/native/mock) + `fetch_url`, autonomous research loop → `ResearchBrief`.
4. **Orchestrator Agent** — ICP qualify + score + reasoning, outreach draft, `Lead` schema.
5. **Persistence + logging** — Postgres models, repositories, `agent_runs`, `request_logs`, Alembic.
6. **API** — `/v1/leads` etc., auth, rate limiting, OpenAPI.
7. **Exporters** — `excel` (folder) first; then `slack`, `email`, `gmail`.
8. **Dashboard UI (React + Vite)** — submit domain, live research, lead + draft view.
9. **Observability** — Langfuse tracing, Prometheus metrics.
10. **n8n** — trigger + human-approval-and-send + alerting workflows.
11. **Deploy** — compose → minikube manifests/Helm.
12. **Quality + polish** — tests, eval harness, CI, portfolio README, demo GIF.

---

## 16. Open Decisions (defaults chosen; revisit if needed)

- **Default LLM provider/model:** OpenRouter as the shipped default value, but the hard
  requirement is **easy switchability** — one ENV line swaps to NVIDIA/OpenAI/Anthropic/local.
- **Default search mode:** `native` (web-first, model does its own search); `mock` for
  offline/zero-key demos; `api`(tavily) when finer control is wanted.
- **Streaming responses:** deferred to a later enhancement (v1 returns full responses).
- **Dashboard stack:** **React (Vite)** — its own build, talks to the FastAPI API.
