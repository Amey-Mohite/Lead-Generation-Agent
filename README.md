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
- [x] Phase 2 — Multi-provider LLM layer (OpenRouter/NVIDIA/OpenAI/Anthropic + fallback)
- [x] Phase 3 — Research sub-agent + tools (ReAct loop, web_search/fetch_url, ResearchBrief)
- [ ] Phase 4 — Orchestrator agent (qualify + draft)
- [ ] Phases 5–12 — persistence, API, exporters, dashboard, observability, n8n, deploy

## Documentation

- **📚 Learning guides** (understand the flow, the why/how) — [`docs/learning/`](docs/learning/README.md)
  - [Phase 1 — Foundations](docs/learning/phase-1-foundations.md)
  - [Phase 2 — Multi-Provider LLM Layer](docs/learning/phase-2-llm-provider-layer.md)
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
