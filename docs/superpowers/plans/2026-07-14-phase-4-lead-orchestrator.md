# Phase 4: Lead Orchestrator Agent — Implementation Plan

> **Execution note:** The user commits/pushes to GitHub themselves. Do **not** run `git commit`
> or `git push`. End each task by reporting exactly what changed for the user to review and commit.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Lead Orchestrator Agent — it runs the Phase 3 `ResearchAgent` to get a
`ResearchBrief`, **qualifies** the company against a config-driven Ideal Customer Profile (a score
+ reasoning), and — only if the score clears a threshold — **drafts** a personalized outreach
email, emitting a validated `Lead`.

**Architecture:** Two separate, single-shot LLM calls (not a tool loop — qualify/draft need no
tools) drive the qualify and draft steps. Both reuse a new shared helper, `complete_structured()`,
that generalizes the "ask for JSON matching a schema, tolerate/retry on bad output" pattern Phase
3's `ResearchAgent` already proved. If the qualification score is below
`Settings.icp_min_score_to_draft`, drafting is skipped entirely (no LLM call, no cost) and the
`Lead` is returned with `status="disqualified"`.

**Tech Stack:** Python 3.12, pydantic. Reuses Phase 2's `LLMProvider`/`ChatMessage`/`FallbackLLM`
and Phase 3's `ResearchAgent`, `ResearchBrief`, `extract_json_object`.

## Global Constraints

- **Python:** 3.12+.
- **No network / no keys in tests:** a scripted fake `LLMProvider` and a fake research agent (just
  `.run(target) -> ResearchBrief`) drive every test. No test may hit the network or need a real key.
- **ICP is config-driven, never hard-coded:** `Settings.icp_description` (free text) and
  `Settings.icp_min_score_to_draft` (int) are the only place ICP criteria live.
- **Qualify and draft are two separate LLM calls.** Never merge them into one prompt.
- **Disqualified leads skip drafting entirely** — no LLM call wasted on a lead below threshold.
- **Reuse, don't duplicate:** compose Phase 3's `ResearchAgent` and Phase 2's `FallbackLLM`/
  `build_llm_provider`; don't reimplement JSON-parsing or self-correction logic that
  `complete_structured()` already generalizes.
- **Every task ends** with: tests green, then report the changes to the user for review/commit.

## File Structure

```
app/
  schemas/
    lead.py               # Qualification, OutreachDraft, Lead
  agents/
    structured.py         # complete_structured() + StructuredOutputError
    orchestrator_agent.py # LeadOrchestratorAgent + build_lead_orchestrator_agent()
  config.py                # + icp_description, icp_min_score_to_draft
scripts/
  try_lead.py              # manual end-to-end demo (research -> qualify -> draft)
tests/
  schemas/test_lead.py
  agents/test_structured.py
  agents/test_orchestrator_agent.py
docs/
  learning/phase-4-lead-orchestrator.md
```

---

### Task 1: `Lead` output schemas

**Files:**
- Create: `app/schemas/lead.py`
- Test: `tests/schemas/test_lead.py`

**Interfaces:**
- Consumes: `ResearchBrief` (`app.schemas.research`).
- Produces:
  - `Qualification(BaseModel)` — `score: int` (constrained `0 <= score <= 100`), `reasoning: str`.
  - `OutreachDraft(BaseModel)` — `subject: str`, `body: str`.
  - `Lead(BaseModel)` — `research: ResearchBrief`, `qualification: Qualification`,
    `outreach: OutreachDraft | None = None`, `status: Literal["qualified", "disqualified"]`.

- [ ] **Step 1: Write the failing test** — `tests/schemas/test_lead.py`

```python
import pytest
from pydantic import ValidationError

from app.schemas.lead import Lead, OutreachDraft, Qualification
from app.schemas.research import ResearchBrief


def _brief() -> ResearchBrief:
    return ResearchBrief(company_name="Acme", summary="Makes widgets.")


def test_qualification_score_bounds():
    Qualification(score=0, reasoning="ok")
    Qualification(score=100, reasoning="ok")
    with pytest.raises(ValidationError):
        Qualification(score=101, reasoning="too high")
    with pytest.raises(ValidationError):
        Qualification(score=-1, reasoning="too low")


def test_lead_disqualified_has_no_outreach():
    lead = Lead(
        research=_brief(),
        qualification=Qualification(score=20, reasoning="Not a fit."),
        status="disqualified",
    )
    assert lead.outreach is None


def test_lead_qualified_with_outreach():
    lead = Lead(
        research=_brief(),
        qualification=Qualification(score=90, reasoning="Great fit."),
        outreach=OutreachDraft(subject="Hi", body="Noticed you make widgets..."),
        status="qualified",
    )
    assert lead.outreach.subject == "Hi"


def test_lead_status_rejects_invalid_value():
    with pytest.raises(ValidationError):
        Lead(
            research=_brief(),
            qualification=Qualification(score=50, reasoning="ok"),
            status="maybe",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/schemas/test_lead.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.schemas.lead'`.

- [ ] **Step 3: Create `app/schemas/lead.py`**

```python
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.research import ResearchBrief


class Qualification(BaseModel):
    score: int = Field(ge=0, le=100)
    reasoning: str


class OutreachDraft(BaseModel):
    subject: str
    body: str


class Lead(BaseModel):
    research: ResearchBrief
    qualification: Qualification
    outreach: OutreachDraft | None = None
    status: Literal["qualified", "disqualified"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/schemas/test_lead.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Report changes** to the user for review/commit.

---

### Task 2: Config-driven ICP settings

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings.icp_description: str` (default: a reasonable generic ICP text),
  `Settings.icp_min_score_to_draft: int` (default `60`).

- [ ] **Step 1: Write the failing test** — add to `tests/test_config.py`

```python
def test_icp_defaults():
    s = Settings(_env_file=None)
    assert "B2B" in s.icp_description or len(s.icp_description) > 0
    assert s.icp_min_score_to_draft == 60


def test_icp_env_override(monkeypatch):
    monkeypatch.setenv("ICP_DESCRIPTION", "Only fintech startups under 50 employees.")
    monkeypatch.setenv("ICP_MIN_SCORE_TO_DRAFT", "80")
    s = Settings(_env_file=None)
    assert s.icp_description == "Only fintech startups under 50 employees."
    assert s.icp_min_score_to_draft == 80
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_config.py -v -k icp`
Expected: FAIL — `AttributeError`/`ValidationError` (fields don't exist yet).

- [ ] **Step 3: Add the fields to `app/config.py`** — insert after the `research_search_mode` /
  `search_provider` / `search_api_key` block:

```python
    # Lead qualification (ICP = Ideal Customer Profile)
    icp_description: str = (
        "A B2B software or technology company with 10-500 employees, "
        "based in North America or Europe."
    )
    icp_min_score_to_draft: int = 60
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: PASS (all config tests, including the 2 new ones).

- [ ] **Step 5: Add to `.env.example`** — after the `RESEARCH_SEARCH_MODE`/`SEARCH_PROVIDER`/
  `SEARCH_API_KEY` block:

```env
# Lead qualification (ICP = Ideal Customer Profile)
ICP_DESCRIPTION=A B2B software or technology company with 10-500 employees, based in North America or Europe.
ICP_MIN_SCORE_TO_DRAFT=60
```

- [ ] **Step 6: Report changes** to the user for review/commit.

---

### Task 3: `complete_structured()` — the shared JSON-plus-retry helper

**Files:**
- Create: `app/agents/structured.py`
- Test: `tests/agents/test_structured.py`

**Interfaces:**
- Consumes: `LLMProvider`, `ChatMessage` (`app.providers.llm.base`); any `pydantic.BaseModel`
  subclass as `schema`.
- Produces:
  - `StructuredOutputError(Exception)`.
  - `complete_structured(llm: LLMProvider, messages: list[ChatMessage], schema: type[BaseModel], *, max_retries: int = 3) -> BaseModel` —
    calls `llm.complete(messages)`, appends the reply as `assistant`, tries to parse+validate it
    into `schema`; on unparseable JSON or a validation error, appends a corrective `user` message
    and retries (up to `max_retries` attempts total); raises `StructuredOutputError` if it never
    succeeds. Mutates `messages` in place (matches `ResearchAgent`'s existing style).

- [ ] **Step 1: Write the failing test** — `tests/agents/test_structured.py`

```python
import pytest
from pydantic import BaseModel

from app.agents.structured import StructuredOutputError, complete_structured
from app.providers.llm.base import ChatMessage, LLMResponse


class _Widget(BaseModel):
    name: str
    count: int


class _ScriptedLLM:
    name = "scripted"

    def __init__(self, scripts: list[str]):
        self._scripts = scripts
        self.calls = 0

    def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
        content = self._scripts[self.calls]
        self.calls += 1
        return LLMResponse(content=content, model="scripted", provider="scripted")


def _msgs():
    return [ChatMessage(role="user", content="describe a widget")]


def test_happy_path_parses_first_reply():
    llm = _ScriptedLLM(['{"name": "gadget", "count": 3}'])
    result = complete_structured(llm, _msgs(), _Widget)
    assert result == _Widget(name="gadget", count=3)


def test_recovers_from_non_json_reply():
    llm = _ScriptedLLM(["not json at all", '{"name": "gadget", "count": 3}'])
    result = complete_structured(llm, _msgs(), _Widget)
    assert result == _Widget(name="gadget", count=3)
    assert llm.calls == 2


def test_recovers_from_validation_error():
    llm = _ScriptedLLM(['{"name": "gadget"}', '{"name": "gadget", "count": 3}'])
    result = complete_structured(llm, _msgs(), _Widget)
    assert result == _Widget(name="gadget", count=3)


def test_raises_after_max_retries():
    llm = _ScriptedLLM(["nope", "still nope", "nope again"])
    with pytest.raises(StructuredOutputError):
        complete_structured(llm, _msgs(), _Widget, max_retries=3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_structured.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.structured'`.

- [ ] **Step 3: Create `app/agents/structured.py`**

```python
from pydantic import BaseModel, ValidationError

from app.agents.json_utils import extract_json_object
from app.providers.llm.base import ChatMessage, LLMProvider


class StructuredOutputError(Exception):
    pass


def complete_structured(
    llm: LLMProvider,
    messages: list[ChatMessage],
    schema: type[BaseModel],
    *,
    max_retries: int = 3,
) -> BaseModel:
    for _ in range(max_retries):
        resp = llm.complete(messages)
        messages.append(ChatMessage(role="assistant", content=resp.content))
        parsed = extract_json_object(resp.content)

        if parsed is None:
            messages.append(
                ChatMessage(
                    role="user",
                    content="That was not valid JSON. Reply with ONE JSON object only.",
                )
            )
            continue

        try:
            return schema(**parsed)
        except ValidationError as exc:
            messages.append(
                ChatMessage(role="user", content=f"That was invalid ({exc}). Fix and resend.")
            )
            continue

    raise StructuredOutputError(
        f"Could not get a valid {schema.__name__} within {max_retries} attempts."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_structured.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Report changes** to the user for review/commit.

---

### Task 4: `LeadOrchestratorAgent` — qualify, then conditionally draft

**Files:**
- Create: `app/agents/orchestrator_agent.py`
- Test: `tests/agents/test_orchestrator_agent.py`

**Interfaces:**
- Consumes: `LLMProvider`, `ChatMessage`; `complete_structured` (Task 3); `Qualification`,
  `OutreachDraft`, `Lead` (Task 1); a research-agent object satisfying `run(target: str) ->
  ResearchBrief` (matches `ResearchAgent.run`'s exact signature from Phase 3 — any object with that
  method works, real or fake).
- Produces: `LeadOrchestratorAgent(llm: LLMProvider, research_agent, icp_description: str,
  min_score_to_draft: int = 60)` with `.run(target: str) -> Lead`.

- [ ] **Step 1: Write the failing test** — `tests/agents/test_orchestrator_agent.py`

```python
from app.agents.orchestrator_agent import LeadOrchestratorAgent
from app.providers.llm.base import LLMResponse
from app.schemas.research import ResearchBrief


class _FakeResearchAgent:
    def __init__(self, brief: ResearchBrief):
        self._brief = brief

    def run(self, target: str) -> ResearchBrief:
        return self._brief


class _ScriptedLLM:
    name = "scripted"

    def __init__(self, scripts: list[str]):
        self._scripts = scripts
        self.calls: list[str] = []

    def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
        content = self._scripts[len(self.calls)]
        self.calls.append(content)
        return LLMResponse(content=content, model="scripted", provider="scripted")


def _brief() -> ResearchBrief:
    return ResearchBrief(company_name="Acme", summary="Makes widgets for other businesses.")


def test_qualified_lead_gets_a_draft():
    scripts = [
        '{"score": 85, "reasoning": "Strong B2B fit."}',
        '{"subject": "Quick question", "body": "Hi -- noticed Acme makes widgets..."}',
    ]
    agent = LeadOrchestratorAgent(
        _ScriptedLLM(scripts), _FakeResearchAgent(_brief()),
        icp_description="B2B companies", min_score_to_draft=60,
    )
    lead = agent.run("acme.com")

    assert lead.status == "qualified"
    assert lead.qualification.score == 85
    assert lead.outreach is not None
    assert lead.outreach.subject == "Quick question"


def test_disqualified_lead_skips_the_draft_call():
    scripts = ['{"score": 20, "reasoning": "Not a B2B fit."}']
    llm = _ScriptedLLM(scripts)
    agent = LeadOrchestratorAgent(
        llm, _FakeResearchAgent(_brief()),
        icp_description="B2B companies", min_score_to_draft=60,
    )
    lead = agent.run("acme.com")

    assert lead.status == "disqualified"
    assert lead.outreach is None
    assert len(llm.calls) == 1  # draft LLM call never happened


def test_icp_description_is_included_in_the_qualify_prompt():
    captured: dict = {}

    class _CapturingLLM:
        name = "capturing"

        def __init__(self):
            self._scripts = ['{"score": 90, "reasoning": "fits"}', '{"subject": "s", "body": "b"}']
            self._i = 0

        def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
            if self._i == 0:
                captured["qualify_system"] = messages[0].content
            content = self._scripts[self._i]
            self._i += 1
            return LLMResponse(content=content, model="scripted", provider="scripted")

    agent = LeadOrchestratorAgent(
        _CapturingLLM(), _FakeResearchAgent(_brief()),
        icp_description="ONLY-FINTECH-MARKER", min_score_to_draft=60,
    )
    agent.run("acme.com")
    assert "ONLY-FINTECH-MARKER" in captured["qualify_system"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_orchestrator_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.orchestrator_agent'`.

- [ ] **Step 3: Create `app/agents/orchestrator_agent.py`**

```python
from app.agents.structured import complete_structured
from app.providers.llm.base import ChatMessage, LLMProvider
from app.schemas.lead import Lead, OutreachDraft, Qualification
from app.schemas.research import ResearchBrief

_QUALIFY_SYSTEM = """You are a lead qualification agent. Score how well a company fits an Ideal
Customer Profile (ICP), based on its research brief.

ICP:
{icp_description}

Respond with ONE JSON object and nothing else:
{{"score": <integer 0-100>, "reasoning": "..."}}

Rules:
- Score 0 = not a fit at all, 100 = a perfect fit.
- Base your reasoning only on the research brief provided -- do not invent facts.
- "score" and "reasoning" are both required."""

_DRAFT_SYSTEM = """You are a sales development representative drafting a first-touch outreach
email. Write a short, personalized message based on the company's research brief and why it
qualifies as a good fit.

Respond with ONE JSON object and nothing else:
{"subject": "...", "body": "..."}

Rules:
- Reference at least one specific fact from the research brief -- do not write a generic email.
- Keep the body under 6 sentences.
- Do not invent facts not present in the research brief or the qualification reasoning.
- "subject" and "body" are both required."""


class LeadOrchestratorAgent:
    def __init__(
        self,
        llm: LLMProvider,
        research_agent,
        icp_description: str,
        min_score_to_draft: int = 60,
    ) -> None:
        self._llm = llm
        self._research_agent = research_agent
        self._icp_description = icp_description
        self._min_score_to_draft = min_score_to_draft

    def run(self, target: str) -> Lead:
        brief = self._research_agent.run(target)
        qualification = self._qualify(brief)

        if qualification.score < self._min_score_to_draft:
            return Lead(
                research=brief, qualification=qualification, outreach=None, status="disqualified"
            )

        outreach = self._draft(brief, qualification)
        return Lead(
            research=brief, qualification=qualification, outreach=outreach, status="qualified"
        )

    def _qualify(self, brief: ResearchBrief) -> Qualification:
        messages = [
            ChatMessage(
                role="system",
                content=_QUALIFY_SYSTEM.format(icp_description=self._icp_description),
            ),
            ChatMessage(
                role="user", content=f"Research brief:\n{brief.model_dump_json(indent=2)}"
            ),
        ]
        result = complete_structured(self._llm, messages, Qualification)
        assert isinstance(result, Qualification)
        return result

    def _draft(self, brief: ResearchBrief, qualification: Qualification) -> OutreachDraft:
        messages = [
            ChatMessage(role="system", content=_DRAFT_SYSTEM),
            ChatMessage(
                role="user",
                content=(
                    f"Research brief:\n{brief.model_dump_json(indent=2)}\n\n"
                    f"Why this company qualifies:\n{qualification.reasoning}"
                ),
            ),
        ]
        result = complete_structured(self._llm, messages, OutreachDraft)
        assert isinstance(result, OutreachDraft)
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_orchestrator_agent.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Report changes** to the user for review/commit.

---

### Task 5: `build_lead_orchestrator_agent(settings)` — assemble from config

**Files:**
- Modify: `app/agents/orchestrator_agent.py` (add the factory function)
- Test: `tests/agents/test_orchestrator_agent.py` (add one test)

**Interfaces:**
- Consumes: `build_research_agent`, `build_llm_provider`, `FallbackLLM` (Phases 2/3).
- Produces: `build_lead_orchestrator_agent(settings: Settings) -> LeadOrchestratorAgent`.

- [ ] **Step 1: Write the failing test** — add these two imports to the top of
  `tests/agents/test_orchestrator_agent.py` (alongside the existing
  `from app.agents.orchestrator_agent import LeadOrchestratorAgent` from Task 4):

```python
from app.agents.orchestrator_agent import build_lead_orchestrator_agent
from app.config import Settings
```

Then append this test to the same file:

```python
def test_build_lead_orchestrator_agent_from_settings():
    s = Settings(
        _env_file=None,
        llm_provider="openrouter",
        llm_model="test-model",
        openrouter_api_key="k",
        research_search_mode="mock",
        icp_description="Test ICP",
        icp_min_score_to_draft=70,
    )
    agent = build_lead_orchestrator_agent(s)
    assert isinstance(agent, LeadOrchestratorAgent)
    assert agent._icp_description == "Test ICP"
    assert agent._min_score_to_draft == 70
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_orchestrator_agent.py -v -k build`
Expected: FAIL — `ImportError: cannot import name 'build_lead_orchestrator_agent'`.

- [ ] **Step 3: Add the factory to `app/agents/orchestrator_agent.py`** (append at the end of the
  file):

```python
def build_lead_orchestrator_agent(settings) -> "LeadOrchestratorAgent":
    from app.agents.research_agent import build_research_agent
    from app.providers.llm.factory import build_llm_provider
    from app.providers.llm.fallback import FallbackLLM

    research_agent = build_research_agent(settings)
    llm = FallbackLLM(build_llm_provider(settings), settings.llm_fallback_model)
    return LeadOrchestratorAgent(
        llm=llm,
        research_agent=research_agent,
        icp_description=settings.icp_description,
        min_score_to_draft=settings.icp_min_score_to_draft,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/agents/test_orchestrator_agent.py -v`
Expected: PASS (4 passed total in this file).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all Phase 1-4 tests green (52 prior + this phase's new tests).

- [ ] **Step 6: Report changes** to the user for review/commit.

---

### Task 6: `scripts/try_lead.py` — manual end-to-end demo

**Files:**
- Create: `scripts/try_lead.py`

**Interfaces:**
- Consumes: `LeadOrchestratorAgent`, `build_lead_orchestrator_agent`, `get_settings`,
  `ResearchBrief`, `LLMResponse` — same auto-detect pattern as `scripts/try_research.py`.
- Produces: a runnable script printing a full `Lead` (research → qualify → draft) in offline-demo
  mode (no keys) or real mode (via config).

- [ ] **Step 1: Create `scripts/try_lead.py`**

```python
"""Manual end-to-end check of the Lead Orchestrator Agent (research -> qualify -> draft).

Usage:
    ./.venv/Scripts/python.exe scripts/try_lead.py [target] [--demo]

Behaviour:
- If an API key for the configured LLM_PROVIDER is present, does a REAL run: real research
  (respecting RESEARCH_SEARCH_MODE), real qualification against ICP_DESCRIPTION, and a real
  outreach draft if the score clears ICP_MIN_SCORE_TO_DRAFT.
- If no key is found (or --demo is passed), runs an OFFLINE scripted demo (no network, no keys).
"""

import sys

from app.agents.orchestrator_agent import LeadOrchestratorAgent
from app.config import get_settings
from app.providers.llm.base import LLMResponse
from app.schemas.research import ResearchBrief

_KEY_ATTR = {
    "openrouter": "openrouter_api_key",
    "nvidia": "nvidia_api_key",
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
}


class _ScriptedResearchAgent:
    def __init__(self, target: str) -> None:
        self._target = target

    def run(self, target: str) -> ResearchBrief:
        return ResearchBrief(
            company_name=target,
            domain=target,
            industry="(demo)",
            summary=f"Offline demo brief for {target}.",
            key_facts=["This is a scripted demo, not real research."],
            sources=["https://example.com"],
        )


class _ScriptedLLM:
    name = "scripted-demo"

    def __init__(self) -> None:
        self._scripts = [
            '{"score": 82, "reasoning": '
            '"Demo company matches the ICP closely enough for this scripted run."}',
            '{"subject": "Quick question for you", '
            '"body": "Hi there -- noticed your work and wanted to reach out. '
            '(This is a scripted demo body.)"}',
        ]
        self._i = 0

    def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
        content = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return LLMResponse(content=content, model="scripted", provider="scripted")


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--demo"]
    force_demo = "--demo" in sys.argv
    target = args[0] if args else "stripe.com"

    settings = get_settings()
    key_attr = _KEY_ATTR.get(settings.llm_provider)
    has_key = bool(getattr(settings, key_attr, None)) if key_attr else False

    if has_key and not force_demo:
        from app.agents.orchestrator_agent import build_lead_orchestrator_agent

        print(
            f"[REAL run] provider={settings.llm_provider} model={settings.llm_model} "
            f"search_mode={settings.research_search_mode} target={target}"
        )
        print(f"ICP: {settings.icp_description}")
        print(f"Min score to draft: {settings.icp_min_score_to_draft}")
        agent = build_lead_orchestrator_agent(settings)
    else:
        why = "forced --demo" if force_demo else f"no API key for '{settings.llm_provider}'"
        print(
            f"[OFFLINE demo] ({why}) target={target}\n"
            f"  -> set a key in .env (e.g. OPENROUTER_API_KEY) for a real run."
        )
        agent = LeadOrchestratorAgent(
            _ScriptedLLM(),
            _ScriptedResearchAgent(target),
            icp_description="Any company (demo mode)",
            min_score_to_draft=60,
        )

    lead = agent.run(target)

    print("\n================ LEAD ================")
    print(lead.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it in offline demo mode to verify it works**

Run: `./.venv/Scripts/python.exe scripts/try_lead.py --demo`
Expected: prints `[OFFLINE demo] ...`, then a `LEAD` JSON block with `"status": "qualified"` and a
populated `"outreach"` block (score 82 clears the default 60 threshold).

- [ ] **Step 3: Report changes** to the user for review/commit.

---

### Task 7: Learning guide + index updates

**Files:**
- Create: `docs/learning/phase-4-lead-orchestrator.md`
- Modify: `docs/learning/README.md`
- Modify: `README.md` (Status section)

**Interfaces:** none (documentation only).

- [ ] **Step 1: Write `docs/learning/phase-4-lead-orchestrator.md`** — same structure as the
  Phase 1-3 guides. Must cover:
  - **What & why** — qualify and draft as the "judgment" layer on top of Phase 3's "senses";
    why they're two separate LLM calls, not one; why disqualified leads skip drafting.
  - **The flow** — a diagram: `target -> ResearchAgent.run() -> ResearchBrief -> qualify (LLM
    call) -> Qualification -> [score below threshold? -> Lead(disqualified), stop] -> draft (LLM
    call) -> OutreachDraft -> Lead(qualified)`.
  - **File-by-file walkthrough** — `app/schemas/lead.py` (why `Qualification.score` is bounded
    with `Field(ge=0, le=100)`, why `outreach` is `| None`); `app/agents/structured.py` (why this
    generalizes `ResearchAgent`'s inline self-correction pattern into something reusable, so future
    phases don't reimplement it); `app/agents/orchestrator_agent.py` (why qualify/draft don't need
    the ReAct tool loop -- no tools required, just structured single-shot calls; how the ICP
    description flows from `Settings` into the qualify prompt).
  - **Key concepts table** — config-driven behavior (ICP), the generalized structured-output
    helper (DRY across agents), short-circuiting to save cost (skip drafting on disqualification),
    composing agents (orchestrator wraps `ResearchAgent` rather than reimplementing research).
  - **How to run & test** — `pytest tests/schemas/test_lead.py tests/agents/test_structured.py
    tests/agents/test_orchestrator_agent.py -v` and `scripts/try_lead.py --demo` / real run with
    `.env` keys, explaining what each test proves (bounds validation, retry/self-correction,
    qualified-gets-a-draft, disqualified-skips-the-draft-call-entirely, ICP text reaching the
    prompt).
  - **What's next** — Phase 5, per the design spec: persistence (Postgres) + `agent_runs`/
    `request_logs`, or the Discovery/`LeadSource` layer (per the phase-5 addendum decided during
    brainstorming) -- note both are candidates and which one is picked will be decided when Phase 5
    starts.

- [ ] **Step 2: Update `docs/learning/README.md`** — add a row to the phase-guides table:

```markdown
| [Phase 4 — Lead Orchestrator Agent](phase-4-lead-orchestrator.md) | The "judgment" layer: qualifying a researched company against a config-driven ICP (score + reasoning), then conditionally drafting personalized outreach. Introduces `complete_structured()`, a reusable generalization of Phase 3's self-correcting JSON parsing. |
```

- [ ] **Step 3: Update `README.md`** — change the Phase 4 status line:

```markdown
- [x] Phase 4 — Orchestrator agent (qualify + draft) — config-driven ICP, conditional drafting
```

- [ ] **Step 4: Report changes** to the user for review/commit.

---

## Phase 4 Definition of Done

- `./.venv/Scripts/python.exe -m pytest -q` → all green (Phase 1-4), no network, no keys required.
- `LeadOrchestratorAgent` qualifies every target, and drafts outreach only when the score clears
  `ICP_MIN_SCORE_TO_DRAFT` -- proven by a test asserting the draft LLM call never happens below
  threshold.
- ICP criteria live only in `Settings` (`ICP_DESCRIPTION`, `ICP_MIN_SCORE_TO_DRAFT`) -- changing
  them is a `.env` edit, never a code change.
- `scripts/try_lead.py --demo` runs end-to-end with zero keys and prints a valid `Lead`.
- Learning guide written; README + learning index updated.

**Next phase (planned just-in-time after this one):** Phase 5 -- to be decided between the
Persistence/logging phase (Postgres storage of `Lead`/`agent_runs`/`request_logs`) and the
Discovery/`LeadSource` layer (enumerating many companies for broad queries like "all UK credit
unions"), per the phase-5 addendum agreed during brainstorming.
