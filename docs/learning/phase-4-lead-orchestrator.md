# Phase 4 — Lead Orchestrator Agent (Learning Guide)

> **Goal of this phase:** build the "judgment" layer on top of Phase 3's "senses." The
> `LeadOrchestratorAgent` takes a `ResearchBrief`, scores the company against a config-driven Ideal
> Customer Profile (ICP), and — only if it's a good enough fit — drafts personalized outreach.

---

## 1. What & why

Research produces facts. Someone (or something) still has to decide **"is this actually a lead
worth pursuing, and if so, what do we say to them?"** That's qualification and drafting — two
distinct judgment calls, not a continuation of research.

**Why two separate LLM calls, not one big prompt:** qualifying (score + reasoning) and drafting
(writing an email) are different tasks with different failure modes. Keeping them separate means:
- Each prompt stays focused and easy to tune independently.
- You can **skip drafting entirely** for a bad-fit lead — no wasted LLM call, matching how a real
  sales team works (nobody writes an outreach email before deciding the lead is worth pursuing).

**Why the ICP lives in config, not code:** the whole point of an Ideal Customer Profile is that
it's *client-specific* — one deployment's ICP might be "B2B SaaS, 10-500 employees, North
America/Europe," another's might be "credit unions in the UK." Hard-coding it into the prompt would
mean editing code for every client. `Settings.icp_description` + `Settings.icp_min_score_to_draft`
make it a `.env` edit, consistent with every other "swap via config, not code" decision in this
project (LLM provider, search mode, exporters).

---

## 2. The flow

```
  target = "acme.com"
     │
     ▼
  ResearchAgent.run(target)        ← Phase 3, reused as-is
     │
     ▼
  ResearchBrief
     │
     ▼
  ┌─────────────── qualify (1 LLM call, complete_structured) ───────────────┐
  │  system: ICP description + "respond with {score, reasoning}"            │
  │  user:   the research brief                                             │
  └───────────────────────────────┬──────────────────────────────────────────┘
                                  ▼
                          Qualification(score, reasoning)
                                  │
                     score < ICP_MIN_SCORE_TO_DRAFT ?
                    ┌─────────────┴─────────────┐
                   yes                          no
                    │                            │
                    ▼                            ▼
        Lead(status="disqualified",   ┌──── draft (1 LLM call) ────┐
             outreach=None)          │  system: "write a short,    │
                                     │  personalized email"        │
                                     │  user: brief + reasoning    │
                                     └──────────────┬───────────────┘
                                                    ▼
                                          OutreachDraft(subject, body)
                                                    │
                                                    ▼
                                    Lead(status="qualified", outreach=...)
```

The disqualified branch **never reaches the draft step** — that's the cost-saving short-circuit,
proven by a test asserting the draft LLM call literally never happens (`len(llm.calls) == 1`).

---

## 3. File-by-file walkthrough

### `app/schemas/lead.py` — the structured output
- `Qualification.score` is `Field(ge=0, le=100)` — a bounded integer. If the model returns `150` or
  `-5`, Pydantic validation fails immediately rather than letting a nonsensical score flow
  downstream into a lead-scoring dashboard.
- `Lead.outreach: OutreachDraft | None = None` — `None` is the *expected* state for a disqualified
  lead, not an error. The type signature itself documents "this can legitimately be absent."
- `Lead.status: Literal["qualified", "disqualified"]` — a closed set of two strings, not a bare
  `str`. This guardrails against typos like `"Qualified"` (capital Q) silently creating a third,
  unintended status that downstream code wouldn't handle.

### `app/agents/structured.py` — generalizing Phase 3's self-correction pattern
Phase 3's `ResearchAgent` already had a proven loop: call the model, try to parse JSON, retry with a
corrective message on failure. `complete_structured()` pulls that pattern out into something
reusable for *any* Pydantic schema, not just `ResearchBrief`:

```python
def complete_structured(llm, messages, schema, *, max_retries=3):
    for _ in range(max_retries):
        resp = llm.complete(messages)
        messages.append(ChatMessage(role="assistant", content=resp.content))
        parsed = extract_json_object(resp.content)
        if parsed is None:
            messages.append(ChatMessage(role="user", content="..."))
            continue
        try:
            return schema(**parsed)
        except ValidationError as exc:
            messages.append(ChatMessage(role="user", content=f"...{exc}..."))
            continue
    raise StructuredOutputError(...)
```

Both `_qualify()` and `_draft()` in the orchestrator call this same function with different
`schema` arguments (`Qualification`, `OutreachDraft`). **This is the DRY payoff of noticing a
repeated pattern** — instead of copy-pasting the retry loop twice more, one function serves every
"ask an LLM for JSON matching schema X" need, in this phase and any future one.

### `app/agents/orchestrator_agent.py` — the orchestrator
- `LeadOrchestratorAgent` doesn't do research itself — it takes a `research_agent` as a
  constructor argument and just calls `.run(target)` on it. In production, that's a real
  `ResearchAgent` (via `build_lead_orchestrator_agent`); in tests, it's a tiny fake with the same
  method. **Composition over reimplementation** — Phase 3's agent is reused whole, not duplicated.
- Neither `_qualify()` nor `_draft()` uses the ReAct tool loop from Phase 3. They don't need
  tools — the research is already done; these are single-shot "read this brief, produce this
  structured judgment" calls. Not every LLM-powered step needs to be an agentic loop.
- `_QUALIFY_SYSTEM.format(icp_description=self._icp_description)` — the ICP text is injected into
  the prompt at call time, straight from `Settings`. Change `ICP_DESCRIPTION` in `.env`, and the
  next run qualifies against different criteria with zero code changes.
- `build_lead_orchestrator_agent(settings)` assembles everything from config alone: a
  `ResearchAgent` (via Phase 3's own factory) plus a `FallbackLLM`-wrapped provider (Phase 2) for
  the qualify/draft calls. One function, fully config-driven, same pattern as
  `build_research_agent()`.

---

## 4. Key concepts (transferable)

| Concept | In one line | When to reach for it |
|---------|-------------|----------------------|
| Config-driven behavior | Business criteria (ICP) live in settings, not code | Anything that varies per client/deployment |
| Generalizing a proven pattern | Pull a repeated inline loop into a reusable function | The second time you'd copy-paste the same retry logic |
| Short-circuiting to save cost | Skip an expensive step when an earlier check fails | Any multi-step pipeline with a cheap gate before an expensive step |
| Composing agents | Wrap/reuse an existing agent rather than reimplementing it | Building a "manager" on top of already-working "workers" |
| Not every LLM call needs a tool loop | Single-shot structured output is enough when no tools are needed | Judgment/classification/drafting tasks over already-gathered data |

---

## 5. How to run & test it

```bash
# All Phase 4 tests — no network, no keys (scripted fake LLM + fake research agent)
./.venv/Scripts/python.exe -m pytest tests/schemas/test_lead.py tests/agents/test_structured.py tests/agents/test_orchestrator_agent.py -v
```

### What the tests prove
- `test_lead.py` — `Qualification.score` rejects out-of-range values; `Lead.outreach` can be
  `None`; `Lead.status` rejects any value outside `{"qualified", "disqualified"}`.
- `test_structured.py` — `complete_structured()` handles the happy path, recovers from a non-JSON
  reply, recovers from a schema-validation failure, and raises `StructuredOutputError` after
  exhausting retries.
- `test_orchestrator_agent.py` — a qualifying score gets a draft; a disqualifying score **never
  triggers the draft LLM call** (`len(llm.calls) == 1`); the ICP description text genuinely reaches
  the qualify prompt; `build_lead_orchestrator_agent()` assembles a working agent from `Settings`
  alone, with the ICP fields flowing through correctly.

### Trying it for real
```bash
./.venv/Scripts/python.exe scripts/try_lead.py "stripe.com"
```
Same auto-detect pattern as `scripts/try_research.py`: no key → offline scripted demo (proves the
qualify → draft branch logic with zero setup); with a key → a real end-to-end run (research →
qualify → conditionally draft) using whatever `.env` config is set (`LLM_PROVIDER`,
`RESEARCH_SEARCH_MODE`, `ICP_DESCRIPTION`, `ICP_MIN_SCORE_TO_DRAFT`).

---

## 6. What's next

Phase 4 completes the **single-lead, end-to-end slice**: give it one company, get back a qualified,
drafted `Lead`. Two directions are queued for Phase 5, decided when that phase starts:
- **Persistence** — storing `Lead`, `agent_runs`, and `request_logs` in Postgres (per the original
  design spec's phased build order).
- **Discovery** — a pluggable `LeadSource` layer that enumerates *many* candidate companies for
  broad queries (e.g. "all UK credit unions"), fanning out into this same research → qualify →
  draft pipeline per candidate (the addendum decided during brainstorming).
