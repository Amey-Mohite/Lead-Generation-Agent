# 🤖 Autonomous Lead-Generation Agent

Production-grade, multi-model AI agent that turns a broad query (e.g. "credit unions in the
UK") into real candidate companies, researches each one, qualifies it against an Ideal
Customer Profile, and drafts personalized outreach — exposed as an API, with n8n
human-approval sending.

> Portfolio project demonstrating: multi-agent design · tool-calling · multi-provider
> LLM routing (OpenRouter / NVIDIA / OpenAI / Anthropic, swap via ENV) · Postgres
> observability · FastAPI · Langfuse tracing · Docker → Kubernetes (minikube).

## Status

Built in phases (see `docs/superpowers/plans/`). **Current: Phase 11 — Deploy** ✅

- [x] Phase 1 — Foundations: config, FastAPI, Postgres, health/ready, Docker
- [x] Phase 2 — Multi-provider LLM layer (OpenRouter/NVIDIA/OpenAI/Anthropic + fallback)
- [x] Phase 3 — Research sub-agent + tools (ReAct loop, web_search/fetch_url, ResearchBrief)
- [x] Phase 4 — Orchestrator agent (qualify + draft) — config-driven ICP, conditional drafting
- [x] Phase 5 — Discovery/LeadSource layer (broad-query enumeration → many candidates → batch of Leads)
- [x] Phase 6 — Exporters (Excel first, via a pluggable `Exporter` protocol; Slack/Email/Gmail later)
- [x] Phase 7 — Persistence (Postgres `leads` table via Alembic; permanent domain dedup for Discovery)
- [x] Phase 8 — API layer (background-job-plus-polling FastAPI endpoints; no-op-when-unset API key auth)
- [x] Phase 9 — Observability (Langfuse tracing across all 4 providers; Prometheus `/metrics`; structured JSON logging)
- [x] Phase 10 — n8n integration (approval-status state machine, 2 new API endpoints, push alerting, 3 n8n workflows -- built, not yet run/tested)
- [x] Phase 11 — Deploy (docker-compose: app + Postgres + n8n; deployment docs in [`deploy/README.md`](deploy/README.md); minikube/Kubernetes + Supabase documented as a future path, not built)

Phase 12 ("Quality + polish") is dropped from the roadmap — the project concludes here with the
comprehensive documentation page linked below instead.

## Documentation

- **📚 Learning guides** (understand the flow, the why/how) — [`docs/learning/`](docs/learning/README.md)
  - [Phase 1 — Foundations](docs/learning/phase-1-foundations.md)
  - [Phase 2 — Multi-Provider LLM Layer](docs/learning/phase-2-llm-provider-layer.md)
  - [Phase 3 — Research Sub-Agent](docs/learning/phase-3-research-sub-agent.md)
  - [Phase 4 — Lead Orchestrator Agent](docs/learning/phase-4-lead-orchestrator.md)
  - [Phase 5 — Discovery / LeadSource Layer](docs/learning/phase-5-discovery.md)
  - [Phase 6 — Excel Export](docs/learning/phase-6-excel-export.md)
  - [Phase 7 — Persistence](docs/learning/phase-7-persistence.md)
  - [Phase 8 — API Layer](docs/learning/phase-8-api-layer.md)
  - [Phase 9 — Observability](docs/learning/phase-9-observability.md)
  - [Phase 10 — n8n Integration](docs/learning/phase-10-n8n-integration.md)
  - [Cookbook: How to Add a New Tool](docs/learning/how-to-add-a-tool.md)
- **Design spec** (system-level decisions) — `docs/superpowers/specs/2026-07-07-lead-generation-agent-design.md`
- **Build plans** (step-by-step) — `docs/superpowers/plans/`

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
