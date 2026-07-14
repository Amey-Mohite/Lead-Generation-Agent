# Phase 5 — Discovery / LeadSource Layer (Learning Guide)

> **Goal of this phase:** remove the "you must already know the company" limitation. Give the
> system a broad query — *"credit unions in the UK"* — and it enumerates real candidate companies,
> then fans each one automatically through the existing research → qualify → draft pipeline.

---

## 1. What & why

Phases 1–4 built a genuinely working pipeline — but every run started with `run("stripe.com")`.
That's fine for "process this one lead I already found," but it's the wrong shape for a *lead
**generation*** tool: nobody using this in the real world already has a list of every UK credit
union's domain name memorized. This phase closes that gap.

**Why candidate extraction is one structured LLM call, not an agentic loop:** a raw web search for
"credit unions in the UK" returns a noisy mix — real credit union sites, Wikipedia list pages,
directory sites, news articles, regulator pages. Something has to filter that noise into a clean
list of *actual companies*. We could build another ReAct-style loop (like `ResearchAgent`) that
iteratively searches and decides when it has "enough" — but for a bounded, config-capped task
("give me up to N candidates"), that's unnecessary complexity. A single structured call — "here are
some search results, extract the real companies" — does the job with predictable cost and no
open-ended looping.

**Why fan-out is sequential, not concurrent:** the simplest correct version first. A plain loop
over candidates, each one calling the existing (unmodified) `LeadOrchestratorAgent`, is easy to
read, easy to debug, and easy to log. Concurrency (running several candidates' pipelines at once)
is a legitimate future upgrade once volume actually demands it — but it adds real complexity (rate
limits, shared state) that isn't worth paying for before the sequential version is proven.

**Why `query` is a runtime parameter but `DISCOVERY_MAX_RESULTS` is config:** this mirrors a
distinction already in the codebase — `target` has never been a `Settings` field for
`ResearchAgent`, because it's different every call. `query` is the same kind of thing: you'll
search "credit unions in the UK" today and "fintech startups in Germany" tomorrow, so baking it
into `.env` would mean editing config for every run. `DISCOVERY_MAX_RESULTS` (how many candidates
to return, ever) is a stable operational/cost-control default that *doesn't* change per call —
exactly like `ICP_MIN_SCORE_TO_DRAFT`. Same reasoning, opposite conclusion, because the two values
have different natures.

---

## 2. The flow

```
  query = "credit unions in the UK"
     │
     ▼
  build_lead_source(settings)
  branches on LEAD_SEARCH_MODE -- independent of RESEARCH_SEARCH_MODE
     │
     ├── mode=native ─▶ NativeSearchSource.discover(query, max_results)
     │                    ONE structured call to an OnlineSearchLLM-wrapped model:
     │                    "search the web yourself and find real companies matching this query"
     │                    → CandidateList(...)   (no SearchBackend involved at all)
     │
     └── mode=api/mock ▶ WebSearchSource.discover(query, max_results)
                          raw_results = search_backend.search(query, k=max_results * 3)
                            ← over-fetch, noisy results (directories, Wikipedia, news, ...)
                          ONE structured LLM call: "extract the real companies from these results"
                          → CandidateList(...)
     │
     ▼  (either path lands here)
  candidates[:max_results]   ← capped in code, even if the model overshoots
     │
     ▼
  list[Candidate]
                                          │
                         sequential loop (discover_and_qualify_leads)
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    ▼                     ▼                     ▼
       LeadOrchestratorAgent    LeadOrchestratorAgent    LeadOrchestratorAgent
        .run(candidate_1.domain) .run(candidate_2.domain) .run(candidate_3.domain) ...
       (Phase 4, unmodified -- research → qualify → maybe draft, per candidate)
                    │                     │                     │
                    ▼                     ▼                     ▼
                  Lead                  Lead                  Lead
                    └─────────────────────┴─────────────────────┘
                                          ▼
                                    list[Lead]
```

Note that **nothing about `LeadOrchestratorAgent` changed** to make this work — Phase 5 is purely
additive: a new front door (`WebSearchSource` + a thin sequential loop) feeding the existing engine.

---

## 3. File-by-file walkthrough

### `app/schemas/discovery.py` — the structured-output envelope
`Candidate(name, domain)` is a real, reusable schema — it's what flows out of discovery and into
the pipeline. `CandidateList(candidates: list[Candidate])` exists **only** as the envelope
`complete_structured()` validates a model reply into; nothing outside `lead_source.py` ever touches
it directly. This is the same pattern `Qualification`/`OutreachDraft` played in Phase 4 — a schema
whose whole job is being a validation target for one specific LLM call.

### `app/agents/lead_source.py` — `LeadSource`, `WebSearchSource`, `NativeSearchSource`
- **Over-fetching (`k=max_results * 3`) matters** for `WebSearchSource`. Not every search result is
  a real company — Wikipedia list pages, news articles, and directory sites all show up for a
  category query like "credit unions in the UK." Asking for 3× the target count gives the
  extraction call enough raw material to filter *down* from, rather than starving it with too few
  results to find genuine companies among.
- **The cap is enforced in code (`result.candidates[:max_results]`), not just in the prompt.** The
  prompt says *"return at most N candidates,"* but nothing guarantees the model obeys — a test
  (`test_discover_caps_at_max_results_even_if_model_returns_more`) proves the code-level slice holds
  even when a scripted "misbehaving" model returns 10 candidates for a `max_results=3` request.
  **Never trust a prompt instruction alone for something a test can enforce mechanically.**
- `LeadSource` is a `Protocol` with two implementations: `WebSearchSource` (explicit `SearchBackend`
  + one extraction call — used for `api`/`mock` modes) and `NativeSearchSource` (wraps the LLM in
  Phase 3's `OnlineSearchLLM` and skips `SearchBackend` entirely — used for `native` mode, the
  model does its own live search and returns candidates directly). A future `RegistrySource` (an
  authoritative dataset — e.g. the FCA Mutuals Register for UK credit unions) or `CsvSource` (a
  user-supplied list) can be added later without touching `discovery_pipeline.py` at all.

> **A real bug we found and fixed: don't silently couple two independent settings.** The first cut
> of `build_lead_source()` reused Phase 3's `build_search_backend(settings)` directly — which reads
> `settings.research_search_mode`, a setting that describes the *Research Agent's* behavior, not
> Discovery's. That function's `native → mock` fallback was harmless in Phase 3 (the Research Agent
> never calls it at all in `native` mode — it uses `OnlineSearchLLM` instead), but Discovery called
> it *unconditionally*. The result: a user with `RESEARCH_SEARCH_MODE=native` and a perfectly valid
> Tavily key configured would silently get **fake, canned search data** for every discovery run,
> with no error and no warning. The fix was **`LEAD_SEARCH_MODE`/`LEAD_SEARCH_PROVIDER`/
> `LEAD_SEARCH_API_KEY`** — Discovery's own, fully independent search configuration, plus a genuine
> `NativeSearchSource` so `native` mode means the same thing (the model's own live search) for
> Discovery as it does for Research, instead of silently meaning "fall back to mock." **Lesson:**
> when a new component reuses an existing function, check what assumptions that function's *original
> caller* was allowed to make — they may not transfer to the new caller.

### `app/agents/discovery_pipeline.py` — composing three phases without modifying any of them
- `discover_and_qualify_leads(lead_source, orchestrator, query, max_results)` takes its
  collaborators as **injected objects**, not `settings` — the exact same dependency-injection
  pattern every agent in this project uses (`ResearchAgent`, `LeadOrchestratorAgent`). This is what
  lets the test suite verify sequential fan-out with fake, in-memory objects and zero network calls.
- `run_discovery_pipeline(settings, query, max_results=None)` is the thin, real-world front door:
  it builds the real `WebSearchSource` and the real `LeadOrchestratorAgent` from config, resolves
  `max_results` (explicit override, else `settings.discovery_max_results`), and delegates to
  `discover_and_qualify_leads`. Notice **neither `LeadSource` nor `LeadOrchestratorAgent` needed a
  single code change** for this phase to exist — Phase 5 is a new consumer of Phase 3/4's work, not
  a modification of it.

---

## 4. Key concepts (transferable)

| Concept | In one line | When to reach for it |
|---------|-------------|----------------------|
| Over-fetch, then filter | Ask a noisy source for more than you need, then clean it down | Any time a source's raw output is noisier than your target output |
| Enforce limits in code, not just in prompts | A prompt instruction is a request; a code-level slice is a guarantee | Any bound (count, size, format) that must actually hold |
| Sequential-first, concurrency later | Build the simple, correct version before optimizing for speed | Any fan-out/batch task, until volume actually demands concurrency |
| Config vs. runtime parameters | Stable operational defaults live in `Settings`; per-call inputs never do | Anything that varies meaningfully between individual invocations |
| Composing without modifying | A new phase can be a new consumer of old code, not a change to it | Extending a working system without risking what already works |
| Don't silently couple two settings | Check a reused function's assumptions before calling it from a new place | Any time you reuse existing code from a genuinely different caller |

---

## 5. How to run & test it

```bash
# All Phase 5 tests — no network, no keys (fake search backend + scripted fake LLM + fakes)
./.venv/Scripts/python.exe -m pytest tests/schemas/test_discovery.py tests/agents/test_lead_source.py tests/agents/test_discovery_pipeline.py -v
```

### What the tests prove
- `test_discovery.py` — `CandidateList` holds `Candidate` objects; an empty list is valid.
- `test_lead_source.py` — `WebSearchSource.discover()` and `NativeSearchSource.discover()` each
  make **exactly one** LLM call (`llm.calls == 1` — proving neither is a hidden agentic loop); the
  `max_results` cap holds even against a scripted model that returns more candidates than asked;
  `build_lead_source()` returns the right implementation for each of `LEAD_SEARCH_MODE`'s three
  values (`native` → `NativeSearchSource`, `api`/`mock` → `WebSearchSource`).
- `test_discovery_pipeline.py` — `discover_and_qualify_leads()` visits every discovered candidate,
  in order, through the injected orchestrator (`orchestrator.targets_seen == ["acme.com",
  "beta.com"]`); `run_discovery_pipeline()` uses `settings.discovery_max_results` by default and
  respects an explicit override when given.

### Trying it for real
```bash
./.venv/Scripts/python.exe scripts/try_discovery.py "credit unions in the UK"
```
Same auto-detect pattern as `scripts/try_research.py`/`scripts/try_lead.py`: no key → offline
scripted demo (two canned candidates, proving the multi-lead-from-one-query shape end to end); with
a key → a real run respecting `.env`'s `RESEARCH_SEARCH_MODE`, `DISCOVERY_MAX_RESULTS`, and
`ICP_DESCRIPTION`.

---

## 6. What's next

Phase 5 closes the loop on the **single-lead-generation-campaign** story: name a category, get back
a batch of researched, qualified, and (where appropriate) drafted leads. Phase 6+ moves into
production-hardening, per the original design spec's remaining build order: **persistence**
(storing `Lead`, `agent_runs`, and `request_logs` in Postgres), then the API layer, exporters,
dashboard, observability, n8n, and deployment.
