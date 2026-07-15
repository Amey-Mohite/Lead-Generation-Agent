# 📚 Learning Guide

This folder explains **how the system works and *why* it's built this way** — the
narrative that the specs (design decisions) and plans (build steps) don't fully capture.

Read these if you want to *understand* the code, not just run it.

| Guide | Covers |
|-------|--------|
| [Phase 1 — Foundations](phase-1-foundations.md) | Config, FastAPI app factory, health vs readiness, Postgres, Docker. The skeleton every request rides on. |
| [Phase 2 — Multi-Provider LLM Layer](phase-2-llm-provider-layer.md) | The provider abstraction: how "swap models via ENV" actually works, adapters, factory, fallback. |
| [Phase 3 — Research Sub-Agent](phase-3-research-sub-agent.md) | The first real agent: the ReAct tool-calling loop, tools, search backends, structured output, bounded autonomy — plus an FAQ deep-dive from debugging real runs (message roles, why one action per turn, native vs. api trust trade-offs, how tracing/decorators work). |
| [Phase 4 — Lead Orchestrator Agent](phase-4-lead-orchestrator.md) | The "judgment" layer: qualifying a researched company against a config-driven ICP (score + reasoning), then conditionally drafting personalized outreach. Introduces `complete_structured()`, a reusable generalization of Phase 3's self-correcting JSON parsing. |
| [Phase 5 — Discovery / LeadSource Layer](phase-5-discovery.md) | Closes the "you must already know the company" gap: a broad query becomes many real candidate companies via one structured extraction call, fanned out sequentially through the existing research → qualify → draft pipeline. |
| [Phase 6 — Excel Export](phase-6-excel-export.md) | The first durable output: `list[Lead]` becomes a real, shareable `.xlsx` file via a pluggable `Exporter` protocol — one row per lead, multi-value fields joined into a cell, disqualified leads included for audit visibility. |
| [Phase 7 — Persistence](phase-7-persistence.md) | Durable memory across process runs: every `Lead` is upserted into a Postgres `leads` table by domain, and Discovery uses that same table to permanently skip domains it has already researched (configurable). |
| [Phase 8 — API Layer](phase-8-api-layer.md) | The pipeline becomes a real HTTP service: background-job-plus-polling for slow discovery runs, an in-memory job store, no-op-when-unset API key auth, and read endpoints over the persisted `leads` table. |

## Cookbooks (practical how-tos, not tied to one phase)

| Guide | Covers |
|-------|--------|
| [How to Add a New Tool](how-to-add-a-tool.md) | The `Tool` protocol contract, a 6-step recipe, and a real worked example (`CurrentDateTool`) with tests — for extending the Research Agent or any future agent built the same way. |

## How to use these

Each guide follows the same shape:

1. **What & why** — the problem the phase solves.
2. **The flow** — a diagram of how data/control moves.
3. **File-by-file walkthrough** — what each file does and the reasoning behind it.
4. **Key concepts** — the transferable ideas (with *when* to reach for them).
5. **How to run & test it.**
6. **What's next.**

## The mental model of the whole project

We are building an **autonomous lead-generation agent**. The big pieces, in build order:

```
Phase 1  Foundations ......... a running, observable web service (the "body")
Phase 2  LLM layer ........... the ability to talk to any model (the "voice")
Phase 3  Research agent ...... autonomous tool-using loop (the "senses" + "thinking")
Phase 4  Orchestrator ........ qualify + draft (the "judgment")
Phase 5  Discovery ........... a query becomes many candidates (the "initiative")
Phase 6  Exporters ........... Excel first, pluggable (the "handoff")
Phase 7  Persistence ......... Postgres `leads` table + permanent domain dedup (the "memory")
Phase 8  API layer ........... FastAPI endpoints exposing the pipeline as a background-job service
Phase 9  Dashboard ........... React UI
Phase 10 Observability ....... Langfuse tracing, Prometheus metrics
Phase 11 n8n .................. ingestion, human-approval sending, alerting
Phase 12 Deploy ............... docker-compose -> minikube / Kubernetes
Phase 13 Quality + polish ..... tests, eval harness, CI, demo GIF, README polish
```

Each phase is independently testable and builds on the ones before it. See the main
[README](../../README.md) for current build status.
