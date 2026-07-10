# Phase 3: Research Sub-Agent — Implementation Plan

> **Execution note:** The user commits/pushes to GitHub themselves. Do **not** run `git commit`
> or `git push`. End each task by reporting exactly what changed for the user to review and commit.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an autonomous **Research Sub-Agent** that, given a company target, decides on its own to search the web and read pages (a ReAct tool-calling loop) and returns a validated `ResearchBrief`.

**Architecture:** A prompt-based **ReAct loop** drives an `LLMProvider` (from Phase 2). Each turn, the model returns a single JSON object — either a tool `action` or a `final` brief. The loop parses it, runs the tool via a `ToolRegistry`, feeds the observation back, and repeats until the model finishes or a step cap is hit. Tools (`web_search`, `fetch_url`) sit behind interfaces with **mock backends** so the whole loop is testable with zero network and zero API keys.

**Tech Stack:** Python 3.12, pydantic, httpx (fetch + api search), beautifulsoup4 (HTML→text), pytest. Reuses Phase 2's `LLMProvider`/`ChatMessage`.

## Global Constraints

- **Python:** 3.12+.
- **No network / no keys in tests:** every tool takes an injectable backend/fetcher; the agent loop is tested with a scripted **FakeLLM**. No test may hit the network.
- **Search mode flag:** `RESEARCH_SEARCH_MODE` (`mock` | `api` | `native`). Phase 3 fully implements `mock` (default for tests/demo) and `api` (Tavily via httpx). `native` is accepted but maps to `mock` for now with a documented TODO (needs a web-search-enabled model).
- **Bounded autonomy (guardrail):** the loop MUST stop after `max_steps` to prevent runaway tool-calling / cost.
- **Robust parsing:** the loop must tolerate the model wrapping JSON in ``` fences or adding stray prose, and must recover from one bad turn by asking the model to correct itself.
- **Reuse, don't duplicate:** use `LLMProvider`, `ChatMessage`, `LLMResponse` from `app/providers/llm/base.py`.

## File Structure

```
app/
  schemas/
    __init__.py
    research.py          # Contact, ResearchBrief (validated output)
  tools/
    __init__.py
    base.py              # Tool (Protocol), ToolRegistry
    search/
      __init__.py
      base.py            # SearchResult, SearchBackend (Protocol)
      mock.py            # MockSearchBackend (canned results)
      tavily.py          # TavilySearchBackend (httpx; api mode)
      factory.py         # build_search_backend(settings)
    web_search.py        # WebSearchTool (wraps a SearchBackend)
    fetch_url.py         # FetchUrlTool (injectable fetcher; bs4 text extract)
  agents/
    __init__.py
    json_utils.py        # extract_json_object() — tolerant parser
    research_agent.py    # ResearchAgent (the ReAct loop) + build_research_agent()
tests/
  schemas/test_research.py
  tools/test_tool_registry.py
  tools/test_search_mock.py
  tools/test_web_search.py
  tools/test_fetch_url.py
  agents/test_json_utils.py
  agents/test_research_agent.py
```

---

### Task 1: Output schemas — `Contact`, `ResearchBrief`

**Files:**
- Create: `app/schemas/__init__.py` (empty), `app/schemas/research.py`
- Test: `tests/schemas/__init__.py` (empty), `tests/schemas/test_research.py`

**Interfaces:**
- Produces:
  - `Contact(BaseModel)` — `name: str`, `role: str | None = None`, `email: str | None = None`.
  - `ResearchBrief(BaseModel)` — `company_name: str`, `domain: str | None = None`, `industry: str | None = None`, `summary: str`, `key_facts: list[str] = []`, `contacts: list[Contact] = []`, `sources: list[str] = []`.

- [ ] **Step 1: Write the failing test** — `tests/schemas/test_research.py`

```python
import pytest
from pydantic import ValidationError

from app.schemas.research import Contact, ResearchBrief


def test_minimal_brief_defaults():
    b = ResearchBrief(company_name="Acme", summary="A widgets company.")
    assert b.key_facts == []
    assert b.contacts == []
    assert b.sources == []
    assert b.domain is None


def test_full_brief_with_contacts():
    b = ResearchBrief(
        company_name="Acme",
        domain="acme.com",
        industry="Manufacturing",
        summary="Makes widgets.",
        key_facts=["Founded 1990"],
        contacts=[{"name": "Jane Doe", "role": "CTO", "email": "jane@acme.com"}],
        sources=["https://acme.com"],
    )
    assert b.contacts[0].name == "Jane Doe"
    assert isinstance(b.contacts[0], Contact)


def test_summary_required():
    with pytest.raises(ValidationError):
        ResearchBrief(company_name="Acme")
```

- [ ] **Step 2: Run to verify it fails** — `./.venv/Scripts/python.exe -m pytest tests/schemas/test_research.py -v` → FAIL (module missing).

- [ ] **Step 3: Create the package + schema**

`app/schemas/__init__.py` and `tests/schemas/__init__.py` empty (0 bytes). `app/schemas/research.py`:
```python
from pydantic import BaseModel


class Contact(BaseModel):
    name: str
    role: str | None = None
    email: str | None = None


class ResearchBrief(BaseModel):
    company_name: str
    domain: str | None = None
    industry: str | None = None
    summary: str
    key_facts: list[str] = []
    contacts: list[Contact] = []
    sources: list[str] = []
```

- [ ] **Step 4: Run to verify it passes** — same command → PASS (3 passed).

- [ ] **Step 5: Report changes** to the user (files added, what they do) for review/commit.

---

### Task 2: `Tool` interface + `ToolRegistry`

**Files:**
- Create: `app/tools/__init__.py` (empty), `app/tools/base.py`
- Test: `tests/tools/__init__.py` (empty), `tests/tools/test_tool_registry.py`

**Interfaces:**
- Produces:
  - `Tool(Protocol)` — `name: str`, `description: str`, `run(self, **kwargs) -> str`.
  - `ToolRegistry` — `__init__(self, tools: list[Tool])`; `describe() -> str` (renders `- name: description` lines for the prompt); `run(self, name: str, args: dict) -> str` (looks up + calls; unknown name → returns an error string, does not raise, so the agent can recover).

- [ ] **Step 1: Write the failing test** — `tests/tools/test_tool_registry.py`

```python
from app.tools.base import ToolRegistry


class _Echo:
    name = "echo"
    description = "echoes text"

    def run(self, **kwargs):
        return f"echo: {kwargs.get('text', '')}"


def test_describe_lists_tools():
    reg = ToolRegistry([_Echo()])
    text = reg.describe()
    assert "echo" in text
    assert "echoes text" in text


def test_run_dispatches_to_tool():
    reg = ToolRegistry([_Echo()])
    assert reg.run("echo", {"text": "hi"}) == "echo: hi"


def test_run_unknown_tool_returns_error_string():
    reg = ToolRegistry([_Echo()])
    out = reg.run("missing", {})
    assert "unknown tool" in out.lower()
```

- [ ] **Step 2: Run to verify it fails** — FAIL (module missing).

- [ ] **Step 3: Create `app/tools/base.py`**

```python
from typing import Protocol, runtime_checkable


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str

    def run(self, **kwargs) -> str: ...


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {t.name: t for t in tools}

    def describe(self) -> str:
        return "\n".join(f"- {t.name}: {t.description}" for t in self._tools.values())

    def run(self, name: str, args: dict) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"ERROR: unknown tool {name!r}. Available: {', '.join(self._tools)}"
        try:
            return tool.run(**args)
        except Exception as exc:  # tools must never crash the loop
            return f"ERROR running {name}: {exc}"
```

- [ ] **Step 4: Run to verify it passes** — PASS (3 passed).

- [ ] **Step 5: Report changes** to the user.

---

### Task 3: Search backends + factory

**Files:**
- Modify: `pyproject.toml` (add `httpx>=0.27` to `dependencies` — used directly now)
- Create: `app/tools/search/__init__.py` (empty), `app/tools/search/base.py`, `app/tools/search/mock.py`, `app/tools/search/tavily.py`, `app/tools/search/factory.py`
- Test: `tests/tools/test_search_mock.py`

**Interfaces:**
- Produces:
  - `SearchResult(BaseModel)` — `title: str`, `url: str`, `snippet: str`.
  - `SearchBackend(Protocol)` — `search(self, query: str, k: int = 5) -> list[SearchResult]`.
  - `MockSearchBackend` — returns deterministic canned results derived from the query (no network).
  - `TavilySearchBackend(api_key, client=None)` — calls Tavily via httpx; maps JSON → `SearchResult`s.
  - `build_search_backend(settings) -> SearchBackend` — `api` → Tavily; `mock`/`native`/other → Mock.

- [ ] **Step 1: Add `httpx>=0.27`** to `dependencies` in `pyproject.toml`.

- [ ] **Step 2: Install** — `./.venv/Scripts/python.exe -m pip install -q -e "."` (httpx is likely already present via openai; this makes it explicit).

- [ ] **Step 3: Write the failing test** — `tests/tools/test_search_mock.py`

```python
from app.config import Settings
from app.tools.search.base import SearchResult
from app.tools.search.factory import build_search_backend
from app.tools.search.mock import MockSearchBackend


def test_mock_returns_k_results():
    backend = MockSearchBackend()
    results = backend.search("acme corp", k=3)
    assert len(results) == 3
    assert all(isinstance(r, SearchResult) for r in results)
    assert "acme corp" in results[0].snippet.lower()


def test_factory_defaults_to_mock():
    s = Settings(_env_file=None, research_search_mode="mock")
    assert isinstance(build_search_backend(s), MockSearchBackend)


def test_factory_native_falls_back_to_mock():
    s = Settings(_env_file=None, research_search_mode="native")
    assert isinstance(build_search_backend(s), MockSearchBackend)
```

- [ ] **Step 4: Run to verify it fails** — FAIL (modules missing).

- [ ] **Step 5: Create the search package files**

`app/tools/search/__init__.py` empty. `app/tools/search/base.py`:
```python
from typing import Protocol

from pydantic import BaseModel


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str


class SearchBackend(Protocol):
    def search(self, query: str, k: int = 5) -> list[SearchResult]: ...
```

`app/tools/search/mock.py`:
```python
from app.tools.search.base import SearchResult


class MockSearchBackend:
    """Deterministic offline search results for tests and zero-key demos."""

    def search(self, query: str, k: int = 5) -> list[SearchResult]:
        return [
            SearchResult(
                title=f"Result {i + 1} for {query}",
                url=f"https://example.com/{query.replace(' ', '-').lower()}/{i + 1}",
                snippet=f"Mock snippet {i + 1} about {query}.",
            )
            for i in range(k)
        ]
```

`app/tools/search/tavily.py`:
```python
from app.tools.search.base import SearchResult

_TAVILY_URL = "https://api.tavily.com/search"


class TavilySearchBackend:
    """Web search via the Tavily API (used when RESEARCH_SEARCH_MODE=api)."""

    def __init__(self, api_key: str | None, client=None) -> None:
        self._api_key = api_key
        if client is not None:
            self._client = client
        else:
            import httpx

            self._client = httpx.Client(timeout=15.0)

    def search(self, query: str, k: int = 5) -> list[SearchResult]:
        resp = self._client.post(
            _TAVILY_URL,
            json={"api_key": self._api_key, "query": query, "max_results": k},
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
            )
            for item in data.get("results", [])
        ]
```

`app/tools/search/factory.py`:
```python
from app.config import Settings
from app.tools.search.base import SearchBackend
from app.tools.search.mock import MockSearchBackend
from app.tools.search.tavily import TavilySearchBackend


def build_search_backend(settings: Settings) -> SearchBackend:
    if settings.research_search_mode == "api" and settings.search_provider == "tavily":
        return TavilySearchBackend(api_key=settings.search_api_key)
    # mock | native | anything else -> offline mock (native TODO: web-search model)
    return MockSearchBackend()
```

- [ ] **Step 6: Run to verify it passes** — PASS (3 passed).

- [ ] **Step 7: Report changes** to the user.

---

### Task 4: `web_search` + `fetch_url` tools

**Files:**
- Modify: `pyproject.toml` (add `beautifulsoup4>=4.12` to `dependencies`)
- Create: `app/tools/web_search.py`, `app/tools/fetch_url.py`
- Test: `tests/tools/test_web_search.py`, `tests/tools/test_fetch_url.py`

**Interfaces:**
- Produces:
  - `WebSearchTool(backend: SearchBackend, k: int = 5)` — `name="web_search"`; `run(self, query: str) -> str` returns a formatted string of results (title, url, snippet).
  - `FetchUrlTool(fetcher=None, max_chars: int = 4000)` — `name="fetch_url"`; `run(self, url: str) -> str` fetches HTML (via injectable `fetcher(url)->str`, default httpx) and returns extracted readable text (bs4), truncated to `max_chars`.

- [ ] **Step 1: Add `beautifulsoup4>=4.12`** to `dependencies`; install with `pip install -q -e "."`.

- [ ] **Step 2: Write failing tests**

`tests/tools/test_web_search.py`:
```python
from app.tools.search.mock import MockSearchBackend
from app.tools.web_search import WebSearchTool


def test_web_search_formats_results():
    tool = WebSearchTool(MockSearchBackend(), k=2)
    out = tool.run(query="acme corp")
    assert tool.name == "web_search"
    assert "acme corp" in out.lower()
    assert "https://example.com" in out
    # both results present
    assert out.lower().count("result") >= 2
```

`tests/tools/test_fetch_url.py`:
```python
from app.tools.fetch_url import FetchUrlTool

_HTML = "<html><body><h1>Acme</h1><p>We make widgets.</p><script>x=1</script></body></html>"


def test_fetch_url_extracts_text_without_scripts():
    tool = FetchUrlTool(fetcher=lambda url: _HTML)
    out = tool.run(url="https://acme.com")
    assert tool.name == "fetch_url"
    assert "Acme" in out
    assert "We make widgets." in out
    assert "x=1" not in out  # script content stripped


def test_fetch_url_truncates():
    big = "<p>" + ("a" * 10000) + "</p>"
    tool = FetchUrlTool(fetcher=lambda url: big, max_chars=100)
    out = tool.run(url="https://x.com")
    assert len(out) <= 100
```

- [ ] **Step 3: Run to verify they fail** — FAIL (modules missing).

- [ ] **Step 4: Create `app/tools/web_search.py`**

```python
from app.tools.search.base import SearchBackend


class WebSearchTool:
    name = "web_search"
    description = "Search the web for a query. Args: {\"query\": string}. Returns titles, urls, snippets."

    def __init__(self, backend: SearchBackend, k: int = 5) -> None:
        self._backend = backend
        self._k = k

    def run(self, query: str) -> str:
        results = self._backend.search(query, k=self._k)
        if not results:
            return "No results."
        lines = [f"{i + 1}. {r.title}\n   {r.url}\n   {r.snippet}" for i, r in enumerate(results)]
        return "\n".join(lines)
```

- [ ] **Step 5: Create `app/tools/fetch_url.py`**

```python
from collections.abc import Callable


def _default_fetcher(url: str) -> str:
    import httpx

    resp = httpx.get(url, timeout=15.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


class FetchUrlTool:
    name = "fetch_url"
    description = "Fetch a web page and return its readable text. Args: {\"url\": string}."

    def __init__(self, fetcher: Callable[[str], str] | None = None, max_chars: int = 4000) -> None:
        self._fetcher = fetcher or _default_fetcher
        self._max_chars = max_chars

    def run(self, url: str) -> str:
        from bs4 import BeautifulSoup

        html = self._fetcher(url)
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        return text[: self._max_chars]
```

- [ ] **Step 6: Run to verify they pass** — PASS (3 passed).

- [ ] **Step 7: Report changes** to the user.

---

### Task 5: The ReAct research loop

**Files:**
- Create: `app/agents/__init__.py` (empty), `app/agents/json_utils.py`, `app/agents/research_agent.py`
- Test: `tests/agents/__init__.py` (empty), `tests/agents/test_json_utils.py`, `tests/agents/test_research_agent.py`

**Interfaces:**
- Consumes: `LLMProvider`, `ChatMessage` (Phase 2); `ToolRegistry`; `ResearchBrief`.
- Produces:
  - `extract_json_object(text: str) -> dict | None` — pulls the first `{...}` JSON object from text, tolerating ``` fences / surrounding prose; returns `None` if not parseable.
  - `ResearchAgent(llm: LLMProvider, registry: ToolRegistry, max_steps: int = 6)` — `run(self, target: str) -> ResearchBrief`. Runs the ReAct loop; on a `final`, validates into `ResearchBrief`; on a bad/unparseable turn, appends a correction message and continues; on exceeding `max_steps`, raises `ResearchError`.
  - `ResearchError(Exception)`.

- [ ] **Step 1: Write failing tests**

`tests/agents/test_json_utils.py`:
```python
from app.agents.json_utils import extract_json_object


def test_plain_json():
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_fenced_json_with_prose():
    text = 'Sure!\n```json\n{"tool": "web_search", "args": {"query": "x"}}\n```\ndone'
    assert extract_json_object(text) == {"tool": "web_search", "args": {"query": "x"}}


def test_unparseable_returns_none():
    assert extract_json_object("no json here") is None
```

`tests/agents/test_research_agent.py`:
```python
import pytest

from app.agents.research_agent import ResearchAgent, ResearchError
from app.providers.llm.base import LLMResponse
from app.tools.base import ToolRegistry
from app.tools.search.mock import MockSearchBackend
from app.tools.web_search import WebSearchTool


class _ScriptedLLM:
    """Returns pre-scripted assistant contents, one per call."""

    name = "scripted"

    def __init__(self, scripts: list[str]):
        self._scripts = scripts
        self.calls = 0

    def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
        content = self._scripts[self.calls]
        self.calls += 1
        return LLMResponse(content=content, model="scripted", provider="scripted")


def _registry():
    return ToolRegistry([WebSearchTool(MockSearchBackend(), k=2)])


def test_happy_path_search_then_final():
    scripts = [
        '{"action": {"tool": "web_search", "args": {"query": "acme corp"}}}',
        '{"final": {"company_name": "Acme", "summary": "Makes widgets.", '
        '"sources": ["https://example.com"]}}',
    ]
    agent = ResearchAgent(_ScriptedLLM(scripts), _registry(), max_steps=5)
    brief = agent.run("acme.com")
    assert brief.company_name == "Acme"
    assert brief.summary == "Makes widgets."


def test_recovers_from_one_bad_turn():
    scripts = [
        "I think I should search...",  # no JSON -> correction, continue
        '{"final": {"company_name": "Acme", "summary": "ok"}}',
    ]
    agent = ResearchAgent(_ScriptedLLM(scripts), _registry(), max_steps=5)
    brief = agent.run("acme.com")
    assert brief.company_name == "Acme"


def test_raises_when_max_steps_exceeded():
    # always searches, never finalizes
    search = '{"action": {"tool": "web_search", "args": {"query": "x"}}}'
    agent = ResearchAgent(_ScriptedLLM([search] * 10), _registry(), max_steps=3)
    with pytest.raises(ResearchError):
        agent.run("acme.com")
```

- [ ] **Step 2: Run to verify they fail** — FAIL (modules missing).

- [ ] **Step 3: Create `app/agents/json_utils.py`**

```python
import json


def extract_json_object(text: str) -> dict | None:
    """Extract the first balanced {...} JSON object from text, tolerating fences/prose."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    obj = json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                return obj if isinstance(obj, dict) else None
    return None
```

- [ ] **Step 4: Create `app/agents/research_agent.py`**

```python
from pydantic import ValidationError

from app.agents.json_utils import extract_json_object
from app.providers.llm.base import ChatMessage, LLMProvider
from app.schemas.research import ResearchBrief
from app.tools.base import ToolRegistry


class ResearchError(Exception):
    pass


_SYSTEM = """You are a research agent. Research the company target the user gives you and \
produce a structured brief.

You can use these tools:
{tools}

On EACH turn respond with ONE JSON object and nothing else, in one of two forms:
1. Use a tool:   {{"action": {{"tool": "<name>", "args": {{...}}}}}}
2. Finish:       {{"final": {{"company_name": "...", "domain": "...", "industry": "...", \
"summary": "...", "key_facts": ["..."], "contacts": [{{"name": "...", "role": "...", \
"email": "..."}}], "sources": ["..."]}}}}

Rules:
- Only ONE action per turn. Base every fact on tool observations; do not invent.
- "company_name" and "summary" are required in the final brief.
- Finish within {max_steps} steps."""


class ResearchAgent:
    def __init__(self, llm: LLMProvider, registry: ToolRegistry, max_steps: int = 6) -> None:
        self._llm = llm
        self._registry = registry
        self._max_steps = max_steps

    def run(self, target: str) -> ResearchBrief:
        system = _SYSTEM.format(tools=self._registry.describe(), max_steps=self._max_steps)
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=f"Research this company: {target}"),
        ]

        for _ in range(self._max_steps):
            resp = self._llm.complete(messages)
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

            if "final" in parsed:
                try:
                    return ResearchBrief(**parsed["final"])
                except ValidationError as exc:
                    messages.append(
                        ChatMessage(
                            role="user",
                            content=f"The final brief was invalid ({exc}). Fix and resend.",
                        )
                    )
                    continue

            action = parsed.get("action") or {}
            observation = self._registry.run(action.get("tool", ""), action.get("args", {}))
            messages.append(ChatMessage(role="user", content=f"Observation:\n{observation}"))

        raise ResearchError(f"Research did not finish within {self._max_steps} steps.")
```

- [ ] **Step 5: Run to verify they pass** — `./.venv/Scripts/python.exe -m pytest tests/agents -v` → PASS (6 passed).

- [ ] **Step 6: Report changes** to the user.

---

### Task 6: Assembly factory + docs

**Files:**
- Modify: `app/agents/research_agent.py` (add `build_research_agent`)
- Create: `docs/learning/phase-3-research-sub-agent.md`
- Modify: `README.md` (Status: mark Phase 3), `docs/learning/README.md` (add row)
- Test: `tests/agents/test_build_research_agent.py`

**Interfaces:**
- Produces: `build_research_agent(settings: Settings) -> ResearchAgent` — wires `build_llm_provider(settings)` (wrapped in `FallbackLLM` with `settings.llm_fallback_model`) + a `ToolRegistry` of `WebSearchTool(build_search_backend(settings))` and `FetchUrlTool()`.

- [ ] **Step 1: Write the failing test** — `tests/agents/test_build_research_agent.py`

```python
from app.agents.research_agent import ResearchAgent, build_research_agent
from app.config import Settings


def test_build_research_agent_from_settings():
    s = Settings(
        _env_file=None,
        llm_provider="openrouter",
        llm_model="test-model",
        openrouter_api_key="k",
        research_search_mode="mock",
    )
    agent = build_research_agent(s)
    assert isinstance(agent, ResearchAgent)
```

- [ ] **Step 2: Run to verify it fails** — FAIL (`build_research_agent` missing).

- [ ] **Step 3: Add `build_research_agent` to `app/agents/research_agent.py`**

```python
def build_research_agent(settings) -> "ResearchAgent":
    from app.providers.llm.factory import build_llm_provider
    from app.providers.llm.fallback import FallbackLLM
    from app.tools.base import ToolRegistry
    from app.tools.fetch_url import FetchUrlTool
    from app.tools.search.factory import build_search_backend
    from app.tools.web_search import WebSearchTool

    llm = FallbackLLM(build_llm_provider(settings), settings.llm_fallback_model)
    registry = ToolRegistry(
        [WebSearchTool(build_search_backend(settings)), FetchUrlTool()]
    )
    return ResearchAgent(llm, registry)
```

- [ ] **Step 4: Run to verify it passes** — PASS (1 passed).

- [ ] **Step 5: Run the FULL suite** — `./.venv/Scripts/python.exe -m pytest -q` → all Phase 1+2+3 green (~35 tests).

- [ ] **Step 6: Write `docs/learning/phase-3-research-sub-agent.md`** — same structure as the other learning guides: what & why, the ReAct loop flow diagram, file-by-file walkthrough (tools, backends, the loop, json_utils, guardrails), key concepts (ReAct, tool registry, bounded autonomy, tolerant parsing, injectable backends), how to run/test, what's next (Phase 4 orchestrator). Update `docs/learning/README.md` table and `README.md` Status (`- [x] Phase 3 — Research sub-agent ...`).

- [ ] **Step 7: Report all changes** to the user for review/commit.

---

## Phase 3 Definition of Done

- `./.venv/Scripts/python.exe -m pytest -q` → all green (Phase 1 + 2 + 3, ~35 tests), no network, no keys.
- The ReAct loop demonstrably: (a) calls a tool then finalizes, (b) recovers from a bad turn, (c) stops at `max_steps`.
- `RESEARCH_SEARCH_MODE` selects mock vs api search backend by config.
- `build_research_agent(settings)` assembles a working agent from ENV alone.
- Learning guide for Phase 3 written; README + learning index updated.

**Next phase (planned just-in-time):** Phase 4 — the Lead Orchestrator Agent: consume the `ResearchBrief`, qualify the lead against an ICP (score + reasoning), and draft personalized outreach → a validated `Lead`.
