# Phase 5: Discovery / LeadSource Layer — Implementation Plan

> **Execution note:** The user commits/pushes to GitHub themselves. Do **not** run `git commit`
> or `git push`. End each task by reporting exactly what changed for the user to review and commit.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the "you must already know the company" limitation. Given a broad query like
*"credit unions in the UK"*, enumerate real candidate companies and fan each one automatically
through the existing Phase 3/4 pipeline (research → qualify → draft), producing a batch of `Lead`s
instead of requiring one URL at a time.

**Architecture:** A `LeadSource` protocol (mirrors `SearchBackend`/`LLMProvider`'s
interface-plus-factory pattern) with one implementation this phase: `WebSearchSource`. It reuses
Phase 3's `SearchBackend` to over-fetch raw, noisy search results, then makes **one single-shot
structured LLM call** (via Phase 4's `complete_structured()`) to extract a clean list of genuine
company candidates from that noise — no agentic loop, no iterative searching. A small pipeline
function then loops **sequentially** over each candidate, calling the existing
`LeadOrchestratorAgent` once per candidate.

**Tech Stack:** Python 3.12, pydantic. Reuses Phase 2's `LLMProvider`/`FallbackLLM`, Phase 3's
`SearchBackend`/`build_search_backend`, Phase 4's `complete_structured`/`LeadOrchestratorAgent`/
`build_lead_orchestrator_agent`.

## Global Constraints

- **Python:** 3.12+.
- **No network / no keys in tests:** a fake `SearchBackend`, a scripted fake `LLMProvider`, a fake
  `LeadSource`, and a fake orchestrator drive every test. No test may hit the network or need a
  real key.
- **`query` is a runtime parameter, never a `Settings` field** — matches how `target` already works
  for `ResearchAgent`/`LeadOrchestratorAgent`. `Settings.discovery_max_results` **is** a config
  field — it's a stable operational/cost-control default, not something that varies per call.
- **Candidate extraction is ONE structured LLM call, not an agentic loop.** `WebSearchSource`
  over-fetches raw search results and asks the model to filter/extract in a single shot.
- **Fan-out is sequential** — a plain loop over candidates calling the orchestrator once each. No
  concurrency in this phase.
- **Reuse, don't duplicate:** `WebSearchSource` reuses `build_search_backend`; the pipeline reuses
  `build_lead_orchestrator_agent` and `complete_structured` as-is.
- **Every task ends** with: tests green, then report the changes to the user for review/commit.

## File Structure

```
app/
  schemas/
    discovery.py            # Candidate, CandidateList
  agents/
    lead_source.py           # LeadSource protocol, WebSearchSource, build_lead_source()
    discovery_pipeline.py    # discover_and_qualify_leads(), run_discovery_pipeline()
  config.py                  # + discovery_max_results
scripts/
  try_discovery.py           # manual end-to-end demo (query -> candidates -> N leads)
tests/
  schemas/test_discovery.py
  agents/test_lead_source.py
  agents/test_discovery_pipeline.py
docs/
  learning/phase-5-discovery.md
```

---

### Task 1: `Candidate` / `CandidateList` schemas

**Files:**
- Create: `app/schemas/discovery.py`
- Test: `tests/schemas/test_discovery.py`

**Interfaces:**
- Produces: `Candidate(BaseModel)` — `name: str`, `domain: str`. `CandidateList(BaseModel)` —
  `candidates: list[Candidate]` (the structured-output envelope `complete_structured` validates
  into; not used outside `lead_source.py`).

- [ ] **Step 1: Write the failing test** — `tests/schemas/test_discovery.py`

```python
from app.schemas.discovery import Candidate, CandidateList


def test_candidate_list_holds_candidates():
    cl = CandidateList(candidates=[{"name": "Acme", "domain": "acme.com"}])
    assert cl.candidates[0].name == "Acme"
    assert isinstance(cl.candidates[0], Candidate)


def test_candidate_list_can_be_empty():
    cl = CandidateList(candidates=[])
    assert cl.candidates == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/schemas/test_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.schemas.discovery'`.

- [ ] **Step 3: Create `app/schemas/discovery.py`**

```python
from pydantic import BaseModel


class Candidate(BaseModel):
    name: str
    domain: str


class CandidateList(BaseModel):
    candidates: list[Candidate]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/schemas/test_discovery.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Report changes** to the user for review/commit.

---

### Task 2: `discovery_max_results` config

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings.discovery_max_results: int = 20`.

- [ ] **Step 1: Write the failing test** — add to `tests/test_config.py`

```python
def test_discovery_max_results_default():
    s = Settings(_env_file=None)
    assert s.discovery_max_results == 20


def test_discovery_max_results_env_override(monkeypatch):
    monkeypatch.setenv("DISCOVERY_MAX_RESULTS", "5")
    s = Settings(_env_file=None)
    assert s.discovery_max_results == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_config.py -v -k discovery_max_results`
Expected: FAIL — `AttributeError` (field doesn't exist yet).

- [ ] **Step 3: Add the field to `app/config.py`** — insert after the ICP block:

```python
    # Discovery (broad-query enumeration -> many candidate companies)
    discovery_max_results: int = 20
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: PASS (all config tests, including the 2 new ones).

- [ ] **Step 5: Add to `.env.example`** — after the ICP block:

```env
# Discovery (broad-query enumeration -> many candidate companies)
DISCOVERY_MAX_RESULTS=20
```

- [ ] **Step 6: Report changes** to the user for review/commit.

---

### Task 3: `LeadSource` protocol + `WebSearchSource` + factory

**Files:**
- Create: `app/agents/lead_source.py`
- Test: `tests/agents/test_lead_source.py`

**Interfaces:**
- Consumes: `complete_structured` (Phase 4); `ChatMessage`, `LLMProvider` (Phase 2);
  `SearchBackend` (Phase 3); `Candidate`, `CandidateList` (Task 1).
- Produces:
  - `LeadSource(Protocol)` — `discover(self, query: str, max_results: int) -> list[Candidate]: ...`.
  - `WebSearchSource(search_backend: SearchBackend, llm: LLMProvider)` implementing `LeadSource`.
    Over-fetches `max_results * 3` raw results, asks the model to extract genuine companies via
    `complete_structured(..., CandidateList)`, and returns at most `max_results` candidates (capped
    in code even if the model returns more).
  - `build_lead_source(settings) -> LeadSource` — currently always returns a `WebSearchSource`
    built from `build_search_backend(settings)` + a `FallbackLLM`-wrapped provider.

- [ ] **Step 1: Write the failing test** — `tests/agents/test_lead_source.py`

```python
from app.agents.lead_source import WebSearchSource, build_lead_source
from app.providers.llm.base import LLMResponse
from app.tools.search.base import SearchResult


class _FakeSearchBackend:
    def __init__(self, results: list[SearchResult]):
        self._results = results

    def search(self, query: str, k: int = 5) -> list[SearchResult]:
        return self._results[:k]


class _ScriptedLLM:
    name = "scripted"

    def __init__(self, scripts: list[str]):
        self._scripts = scripts
        self.calls = 0

    def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
        content = self._scripts[self.calls]
        self.calls += 1
        return LLMResponse(content=content, model="scripted", provider="scripted")


def _raw_results() -> list[SearchResult]:
    return [
        SearchResult(
            title="Acme Credit Union", url="https://acme-cu.com", snippet="A credit union..."
        ),
        SearchResult(
            title="List of UK Credit Unions - Wikipedia",
            url="https://en.wikipedia.org/wiki/List",
            snippet="A list of...",
        ),
        SearchResult(
            title="Beta Credit Union", url="https://beta-cu.com", snippet="A credit union..."
        ),
    ]


def test_discover_extracts_candidates_via_one_structured_llm_call():
    scripts = [
        '{"candidates": [{"name": "Acme Credit Union", "domain": "acme-cu.com"}, '
        '{"name": "Beta Credit Union", "domain": "beta-cu.com"}]}'
    ]
    llm = _ScriptedLLM(scripts)
    source = WebSearchSource(_FakeSearchBackend(_raw_results()), llm)

    candidates = source.discover("credit unions in the UK", max_results=5)

    assert len(candidates) == 2
    assert candidates[0].name == "Acme Credit Union"
    assert candidates[0].domain == "acme-cu.com"
    assert llm.calls == 1  # exactly one structured call, no agentic loop


def test_discover_caps_at_max_results_even_if_model_returns_more():
    many = ", ".join(f'{{"name": "C{i}", "domain": "c{i}.com"}}' for i in range(10))
    scripts = ["{" + f'"candidates": [{many}]' + "}"]
    source = WebSearchSource(_FakeSearchBackend(_raw_results()), _ScriptedLLM(scripts))

    candidates = source.discover("credit unions", max_results=3)

    assert len(candidates) == 3


def test_build_lead_source_returns_a_web_search_source():
    from app.config import Settings

    s = Settings(
        _env_file=None,
        llm_provider="openrouter",
        llm_model="test-model",
        openrouter_api_key="k",
        research_search_mode="mock",
    )
    source = build_lead_source(s)
    assert isinstance(source, WebSearchSource)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_lead_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.lead_source'`.

- [ ] **Step 3: Create `app/agents/lead_source.py`**

```python
from typing import Protocol

from app.agents.structured import complete_structured
from app.providers.llm.base import ChatMessage, LLMProvider
from app.schemas.discovery import Candidate, CandidateList
from app.tools.search.base import SearchBackend

_EXTRACT_SYSTEM = """You are a lead discovery agent. Given raw web search results for a query
describing a category of companies, extract a clean list of real, specific companies that match
the query.

Respond with ONE JSON object and nothing else:
{{"candidates": [{{"name": "...", "domain": "..."}}, ...]}}

Rules:
- Only include results that are a specific company's own website -- exclude directories, news
  articles, Wikipedia pages, government/regulator pages, and anything that is not itself a company.
- "domain" is the company's own website domain (e.g. "acme.com"), not a directory or listing page.
- Do not invent companies that are not present in the search results.
- Return at most {max_results} candidates; fewer is fine if that is all that's genuinely present."""


class LeadSource(Protocol):
    def discover(self, query: str, max_results: int) -> list[Candidate]: ...


class WebSearchSource:
    """Discovers candidate companies via web search + one structured extraction call."""

    def __init__(self, search_backend: SearchBackend, llm: LLMProvider) -> None:
        self._search_backend = search_backend
        self._llm = llm

    def discover(self, query: str, max_results: int) -> list[Candidate]:
        raw_results = self._search_backend.search(query, k=max_results * 3)
        formatted = "\n".join(
            f"{i + 1}. {r.title}\n   {r.url}\n   {r.snippet}"
            for i, r in enumerate(raw_results)
        )
        messages = [
            ChatMessage(
                role="system", content=_EXTRACT_SYSTEM.format(max_results=max_results)
            ),
            ChatMessage(
                role="user", content=f"Query: {query}\n\nSearch results:\n{formatted}"
            ),
        ]
        result = complete_structured(self._llm, messages, CandidateList)
        assert isinstance(result, CandidateList)
        return result.candidates[:max_results]


def build_lead_source(settings) -> LeadSource:
    from app.providers.llm.factory import build_llm_provider
    from app.providers.llm.fallback import FallbackLLM
    from app.tools.search.factory import build_search_backend

    search_backend = build_search_backend(settings)
    llm = FallbackLLM(build_llm_provider(settings), settings.llm_fallback_model)
    return WebSearchSource(search_backend, llm)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_lead_source.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Report changes** to the user for review/commit.

---

### Task 4: `discovery_pipeline.py` — sequential fan-out

**Files:**
- Create: `app/agents/discovery_pipeline.py`
- Test: `tests/agents/test_discovery_pipeline.py`

**Interfaces:**
- Consumes: `build_lead_source` (Task 3); `build_lead_orchestrator_agent` (Phase 4); `Lead`
  (Phase 4); a `LeadSource`-shaped object and an orchestrator-shaped object (`.run(target: str) ->
  Lead`) for injection in tests.
- Produces:
  - `discover_and_qualify_leads(lead_source, orchestrator, query: str, max_results: int) ->
    list[Lead]` — calls `lead_source.discover(query, max_results)`, then loops **sequentially**
    calling `orchestrator.run(candidate.domain)` for each candidate.
  - `run_discovery_pipeline(settings, query: str, max_results: int | None = None) -> list[Lead]` —
    builds real components from `settings` (defaulting `max_results` to
    `settings.discovery_max_results` when not given) and delegates to
    `discover_and_qualify_leads`.

- [ ] **Step 1: Write the failing test** — `tests/agents/test_discovery_pipeline.py`

```python
import app.agents.discovery_pipeline as discovery_pipeline_module
from app.agents.discovery_pipeline import discover_and_qualify_leads, run_discovery_pipeline
from app.config import Settings
from app.schemas.discovery import Candidate
from app.schemas.lead import Lead, Qualification
from app.schemas.research import ResearchBrief


def _lead_for(target: str) -> Lead:
    return Lead(
        research=ResearchBrief(company_name=target, summary=f"Summary for {target}"),
        qualification=Qualification(score=80, reasoning="ok"),
        status="qualified",
    )


class _FakeLeadSource:
    def __init__(self, candidates: list[Candidate]):
        self._candidates = candidates

    def discover(self, query: str, max_results: int) -> list[Candidate]:
        return self._candidates[:max_results]


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.targets_seen: list[str] = []

    def run(self, target: str) -> Lead:
        self.targets_seen.append(target)
        return _lead_for(target)


def test_discover_and_qualify_runs_each_candidate_through_the_orchestrator():
    candidates = [
        Candidate(name="Acme", domain="acme.com"),
        Candidate(name="Beta", domain="beta.com"),
    ]
    source = _FakeLeadSource(candidates)
    orchestrator = _FakeOrchestrator()

    leads = discover_and_qualify_leads(source, orchestrator, "credit unions", max_results=2)

    assert len(leads) == 2
    assert orchestrator.targets_seen == ["acme.com", "beta.com"]
    assert all(isinstance(lead, Lead) for lead in leads)


def test_run_discovery_pipeline_uses_settings_default_max_results(monkeypatch):
    fake_source = _FakeLeadSource([Candidate(name="Acme", domain="acme.com")] * 5)
    fake_orchestrator = _FakeOrchestrator()
    monkeypatch.setattr(discovery_pipeline_module, "build_lead_source", lambda settings: fake_source)
    monkeypatch.setattr(
        discovery_pipeline_module, "build_lead_orchestrator_agent", lambda settings: fake_orchestrator
    )

    s = Settings(_env_file=None, discovery_max_results=3)
    leads = run_discovery_pipeline(s, "credit unions")

    assert len(leads) == 3


def test_run_discovery_pipeline_explicit_max_results_overrides_settings(monkeypatch):
    fake_source = _FakeLeadSource([Candidate(name="Acme", domain="acme.com")] * 5)
    fake_orchestrator = _FakeOrchestrator()
    monkeypatch.setattr(discovery_pipeline_module, "build_lead_source", lambda settings: fake_source)
    monkeypatch.setattr(
        discovery_pipeline_module, "build_lead_orchestrator_agent", lambda settings: fake_orchestrator
    )

    s = Settings(_env_file=None, discovery_max_results=3)
    leads = run_discovery_pipeline(s, "credit unions", max_results=1)

    assert len(leads) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_discovery_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.discovery_pipeline'`.

- [ ] **Step 3: Create `app/agents/discovery_pipeline.py`**

```python
from app.agents.lead_source import build_lead_source
from app.agents.orchestrator_agent import build_lead_orchestrator_agent
from app.schemas.lead import Lead


def discover_and_qualify_leads(lead_source, orchestrator, query: str, max_results: int) -> list[Lead]:
    candidates = lead_source.discover(query, max_results)
    return [orchestrator.run(candidate.domain) for candidate in candidates]


def run_discovery_pipeline(settings, query: str, max_results: int | None = None) -> list[Lead]:
    lead_source = build_lead_source(settings)
    orchestrator = build_lead_orchestrator_agent(settings)
    resolved_max = max_results if max_results is not None else settings.discovery_max_results
    return discover_and_qualify_leads(lead_source, orchestrator, query, resolved_max)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_discovery_pipeline.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all Phase 1-5 tests green (66 prior + this phase's new tests).

- [ ] **Step 6: Report changes** to the user for review/commit.

---

### Task 5: `scripts/try_discovery.py` — manual end-to-end demo

**Files:**
- Create: `scripts/try_discovery.py`

**Interfaces:**
- Consumes: `discover_and_qualify_leads`, `run_discovery_pipeline`, `get_settings`, `Candidate`,
  `Lead`, `Qualification`, `ResearchBrief` — same auto-detect pattern as `scripts/try_lead.py`.

- [ ] **Step 1: Create `scripts/try_discovery.py`**

```python
"""Manual end-to-end check of the Discovery layer (query -> candidates -> N leads).

Usage:
    ./.venv/Scripts/python.exe scripts/try_discovery.py ["credit unions in the UK"] [--demo]

Behaviour:
- If an API key for the configured LLM_PROVIDER is present, does a REAL run: real discovery
  (respecting RESEARCH_SEARCH_MODE + DISCOVERY_MAX_RESULTS), then real research/qualify/draft for
  every discovered candidate.
- If no key is found (or --demo is passed), runs an OFFLINE scripted demo (no network, no keys)
  with two canned candidates.
"""

import sys

from app.config import get_settings
from app.schemas.discovery import Candidate
from app.schemas.lead import Lead, Qualification
from app.schemas.research import ResearchBrief

_KEY_ATTR = {
    "openrouter": "openrouter_api_key",
    "nvidia": "nvidia_api_key",
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
}


class _ScriptedLeadSource:
    def discover(self, query: str, max_results: int) -> list[Candidate]:
        demo = [
            Candidate(name="Acme Credit Union", domain="acme-cu-demo.example"),
            Candidate(name="Beta Credit Union", domain="beta-cu-demo.example"),
        ]
        return demo[:max_results]


class _ScriptedOrchestrator:
    def run(self, target: str) -> Lead:
        return Lead(
            research=ResearchBrief(
                company_name=target,
                domain=target,
                industry="(demo)",
                summary=f"Offline demo brief for {target}.",
                key_facts=["This is a scripted demo, not real research."],
                sources=["https://example.com"],
            ),
            qualification=Qualification(
                score=82, reasoning="Demo candidate matches the ICP closely enough for this run."
            ),
            outreach=None,
            status="qualified",
        )


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--demo"]
    force_demo = "--demo" in sys.argv
    query = args[0] if args else "credit unions in the UK"

    settings = get_settings()
    key_attr = _KEY_ATTR.get(settings.llm_provider)
    has_key = bool(getattr(settings, key_attr, None)) if key_attr else False

    if has_key and not force_demo:
        from app.agents.discovery_pipeline import run_discovery_pipeline

        print(
            f"[REAL run] provider={settings.llm_provider} model={settings.llm_model} "
            f"search_mode={settings.research_search_mode} query={query!r} "
            f"max_results={settings.discovery_max_results}"
        )
        print(f"ICP: {settings.icp_description}")
        leads = run_discovery_pipeline(settings, query)
    else:
        why = "forced --demo" if force_demo else f"no API key for '{settings.llm_provider}'"
        print(
            f"[OFFLINE demo] ({why}) query={query!r}\n"
            f"  -> set a key in .env (e.g. OPENROUTER_API_KEY) for a real run."
        )
        from app.agents.discovery_pipeline import discover_and_qualify_leads

        leads = discover_and_qualify_leads(
            _ScriptedLeadSource(), _ScriptedOrchestrator(), query, max_results=2
        )

    print(f"\n================ {len(leads)} LEAD(S) FOUND ================")
    for i, lead in enumerate(leads, start=1):
        print(f"\n--- Lead {i}: {lead.research.company_name} ---")
        print(lead.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it in offline demo mode to verify it works**

Run: `./.venv/Scripts/python.exe scripts/try_discovery.py --demo`
Expected: prints `[OFFLINE demo] ...`, then `2 LEAD(S) FOUND`, with two `Lead` JSON blocks for
"Acme Credit Union" and "Beta Credit Union", both `"status": "qualified"`.

- [ ] **Step 3: Report changes** to the user for review/commit.

---

### Task 6: Learning guide + index updates

**Files:**
- Create: `docs/learning/phase-5-discovery.md`
- Modify: `docs/learning/README.md`
- Modify: `README.md` (Status section)

**Interfaces:** none (documentation only).

- [ ] **Step 1: Write `docs/learning/phase-5-discovery.md`** — same structure as the Phase 1-4
  guides. Must cover:
  - **What & why** — the gap this closes (Phases 1-4 required an already-known URL); why
    candidate extraction is one structured call instead of an agentic loop (predictable cost, no
    need to "decide when to stop searching" for a bounded, config-capped task); why fan-out is
    sequential for now (simplicity first, concurrency is a clearly-scoped future upgrade); why
    `query` is a runtime parameter but `DISCOVERY_MAX_RESULTS` is config (mirrors `target` vs.
    `ICP_DESCRIPTION`'s existing split).
  - **The flow** — a diagram: `query -> WebSearchSource.discover() -> [over-fetch raw search
    results -> one structured LLM extraction call -> Candidate list, capped at max_results] ->
    sequential loop -> LeadOrchestratorAgent.run(candidate.domain) per candidate -> list[Lead]`.
  - **File-by-file walkthrough** — `app/schemas/discovery.py` (why `CandidateList` exists only as
    a structured-output envelope, not used outside `lead_source.py`); `app/agents/lead_source.py`
    (why over-fetching `max_results * 3` raw results before filtering matters -- not every search
    result is a real company; why the cap is enforced in code, not just prompted for, as defense
    against a misbehaving model); `app/agents/discovery_pipeline.py` (why
    `discover_and_qualify_leads` takes injected `lead_source`/`orchestrator` objects rather than
    `settings` directly -- testability, same DI pattern as every other agent in this project; how
    `run_discovery_pipeline` is the thin, settings-driven front door around it).
  - **Key concepts table** — over-fetch-then-filter (noisy source, clean output via one structured
    call), sequential fan-out as the simple default before optimizing for concurrency, config vs.
    runtime parameters (stable operational settings vs. per-call inputs), composing three prior
    phases' agents into a new pipeline without modifying any of them.
  - **How to run & test** — `pytest tests/schemas/test_discovery.py tests/agents/test_lead_source.py
    tests/agents/test_discovery_pipeline.py -v` and `scripts/try_discovery.py --demo` / a real run,
    explaining what each test proves (exactly one LLM call for extraction -- no hidden loop, the
    max_results cap holds even against a misbehaving model, sequential fan-out visits every
    candidate in order, settings-driven defaults vs. explicit overrides).
  - **What's next** — Phase 6+: persistence (Postgres storage of `Lead`, `agent_runs`,
    `request_logs`), then the API layer, exporters, dashboard, observability, n8n, deploy -- per
    the original design spec's remaining phases.

- [ ] **Step 2: Update `docs/learning/README.md`** — add a row to the phase-guides table:

```markdown
| [Phase 5 — Discovery / LeadSource Layer](phase-5-discovery.md) | Closes the "you must already know the company" gap: a broad query becomes many real candidate companies via one structured extraction call, fanned out sequentially through the existing research -> qualify -> draft pipeline. |
```

- [ ] **Step 3: Update `README.md`** — change the Phase 5 status line (it currently reads
  `[ ] Phases 5-12 — persistence, API, exporters, dashboard, observability, n8n, deploy`; split it):

```markdown
- [x] Phase 5 — Discovery/LeadSource layer (broad-query enumeration -> many candidates -> batch of Leads)
- [ ] Phases 6-12 — persistence, API, exporters, dashboard, observability, n8n, deploy
```

- [ ] **Step 4: Report changes** to the user for review/commit.

---

## Phase 5 Definition of Done

- `./.venv/Scripts/python.exe -m pytest -q` → all green (Phase 1-5), no network, no keys required.
- `WebSearchSource.discover()` makes exactly one structured LLM call per invocation -- no agentic
  loop -- and caps results at `max_results` even if the model returns more.
- `discover_and_qualify_leads()` visits every discovered candidate sequentially through the
  existing `LeadOrchestratorAgent`, unmodified from Phase 4.
- `scripts/try_discovery.py --demo` runs end-to-end with zero keys and prints multiple `Lead`s from
  a single query.
- Learning guide written; README + learning index updated.

**Next phase (planned just-in-time after this one):** Phase 6 — persistence: storing `Lead`,
`agent_runs`, and `request_logs` in Postgres, per the original design spec's build order.
