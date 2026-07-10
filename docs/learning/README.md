# 📚 Learning Guide

This folder explains **how the system works and *why* it's built this way** — the
narrative that the specs (design decisions) and plans (build steps) don't fully capture.

Read these if you want to *understand* the code, not just run it.

| Guide | Covers |
|-------|--------|
| [Phase 1 — Foundations](phase-1-foundations.md) | Config, FastAPI app factory, health vs readiness, Postgres, Docker. The skeleton every request rides on. |
| [Phase 2 — Multi-Provider LLM Layer](phase-2-llm-provider-layer.md) | The provider abstraction: how "swap models via ENV" actually works, adapters, factory, fallback. |
| [Phase 3 — Research Sub-Agent](phase-3-research-sub-agent.md) | The first real agent: the ReAct tool-calling loop, tools, search backends, structured output, bounded autonomy — plus an FAQ deep-dive from debugging real runs (message roles, why one action per turn, native vs. api trust trade-offs, how tracing/decorators work). |

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
Phase 5+ Persistence, API, exporters, dashboard, observability, n8n, deploy
```

Each phase is independently testable and builds on the ones before it.
