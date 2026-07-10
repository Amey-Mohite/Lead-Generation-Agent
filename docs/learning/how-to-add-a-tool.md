# How to Add a New Tool (Cookbook)

> This is a practical, step-by-step guide for extending the Research Agent — or any future agent
> built the same way — with a new tool. Read [Phase 3 — Research Sub-Agent](phase-3-research-sub-agent.md)
> first if you haven't: this doc assumes you already understand the `Tool` protocol and
> `ToolRegistry` from its §3 walkthrough.

---

## What a "tool" actually is in this codebase

A tool is any object that satisfies the `Tool` Protocol (`app/tools/base.py`):

```python
@runtime_checkable
class Tool(Protocol):
    name: str
    description: str

    def run(self, **kwargs) -> str: ...
```

That's the entire contract — no base class to inherit, no decorator to register with, just match
the shape:

- **`name`** — the identifier the model uses to select it: `{"action": {"tool": "<name>", ...}}`.
- **`description`** — one line telling the model what it does and its expected argument shape.
  This text goes straight into the system prompt via `ToolRegistry.describe()` — it is the **only**
  documentation the model ever sees, so be precise about argument names and types.
- **`run(**kwargs) -> str`** — does the work and returns a **plain string**, never a dict or object.
  The loop only knows how to feed text back to the model as an `Observation:` — see
  `ResearchAgent.run()`'s `f"Observation:\n{observation}"` line.

---

## The recipe (6 steps)

1. **Decide the contract.** A short name, a one-line description, and the exact argument shape the
   model should send. Keep arguments minimal — every field you add is one more thing the model can
   get wrong.
2. **Make every external dependency injectable.** Accept a client/fetcher/clock as a constructor
   parameter with a real default — exactly like `WebSearchTool(backend)` and
   `FetchUrlTool(fetcher=None)` already do. This is what lets you test the tool with zero network
   calls and no non-determinism.
3. **Write the tool class.** Implement `name`, `description`, `run(...)`. Don't add your own
   try/except for expected failures inside `run()` — let exceptions propagate.
   `ToolRegistry.run()` already wraps every call in a `try/except Exception`, converting a crash
   into an `"ERROR running <tool>: ..."` observation the model can react to (see Phase 3 §3's
   "never raises" design). Keep the tool itself simple; let the registry handle failure.
4. **Write tests first.** Inject a fake dependency, assert `run()` returns the expected string for
   the happy path, and confirm a failure surfaces as a raised exception (which `ToolRegistry` will
   catch) rather than being silently swallowed inside the tool.
5. **Register it wherever tools are assembled** — typically a `ToolRegistry([...])` list, e.g. in
   `build_research_agent()`. Nothing else needs to change: `ResearchAgent` calls
   `self._registry.describe()` every time it builds the system prompt, so a newly registered tool
   is visible to the model automatically.
6. **Re-run the full suite.** A new tool is independent of every other tool — if adding one breaks
   an unrelated test, something about registration order or shared state is wrong.

---

## Worked example: `CurrentDateTool`

The existing tools (`WebSearchTool`, `FetchUrlTool`) both take an argument and wrap an I/O
dependency (a search backend, an HTTP fetcher). To show a different, equally common shape, here's
a tool that takes **no arguments** and wraps something more subtle to test correctly: **the wall
clock.**

**Why this is a good teaching example:** "what's today's date" sounds trivial, but naively calling
`datetime.now()` inside `run()` would make the tool untestable — you can't assert `run() ==
"2026-07-10"` in a test that might run on any date. The fix is the same injectable-dependency
pattern used everywhere else in this project (fake HTTP clients, fake search backends, fake LLMs):
inject the clock itself.

### The tool — `app/tools/current_date.py`

```python
from collections.abc import Callable
from datetime import datetime, timezone


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CurrentDateTool:
    """Reports today's date (UTC) -- useful for judging how recent a fact or source is."""

    name = "current_date"
    description = "Get today's date in UTC (YYYY-MM-DD). Takes no arguments -- call with {}."

    def __init__(self, now_fn: Callable[[], datetime] | None = None) -> None:
        self._now_fn = now_fn or _utc_now

    def run(self) -> str:
        return self._now_fn().strftime("%Y-%m-%d")
```

**Design notes, tying back to the recipe above:**

- **Step 1 (contract):** `name="current_date"`, zero arguments, returns an ISO-ish date string. The
  description explicitly says `"Takes no arguments -- call with {}"` — without that, the model
  might guess it needs a `{"query": ...}` argument the way `web_search` does, and waste a turn
  getting the call shape wrong.
- **Step 2 (injectable dependency):** `now_fn` is a zero-argument callable, defaulting to the real
  clock (`_utc_now`) but overridable in tests. This is the exact same shape as
  `FetchUrlTool(fetcher=None)` — a real default for production, a seam for tests.
- **Step 3 (simplicity):** `run()` has no error handling of its own. It can't really fail (no
  network, no parsing), so there's nothing to catch — but if it *could* fail, the fix would be to
  let the exception propagate, not to swallow it here.
- **Zero-argument tools work fine with the loop.** The model calls `{"action": {"tool":
  "current_date", "args": {}}}`; `ToolRegistry.run()` does `tool.run(**args)` → `tool.run(**{})` →
  `tool.run()` — no special-casing needed anywhere.

### The tests — `tests/tools/test_current_date.py`

```python
from datetime import datetime, timezone

from app.tools.current_date import CurrentDateTool


def test_returns_formatted_date_from_injected_clock():
    fixed = datetime(2026, 7, 10, 15, 30, tzinfo=timezone.utc)
    tool = CurrentDateTool(now_fn=lambda: fixed)

    assert tool.run() == "2026-07-10"
    assert tool.name == "current_date"


def test_default_clock_returns_a_real_date_string():
    tool = CurrentDateTool()
    result = tool.run()

    # loosely validate shape without asserting an exact date (would be flaky otherwise)
    assert len(result) == 10
    assert result[4] == "-" and result[7] == "-"
```

Run it:
```bash
./.venv/Scripts/python.exe -m pytest tests/tools/test_current_date.py -v
```

- **First test** — proves determinism. By injecting a fixed `datetime`, the test asserts an *exact*
  output (`"2026-07-10"`), which would be impossible to assert reliably against the real clock.
  This is the entire point of dependency injection: the *production* code calls the real clock, the
  *test* code calls a fake one, and both paths run through the identical `run()` logic.
- **Second test** — proves the real (non-injected) path still works, without hard-coding today's
  date (that would make the test fail every single day it's run on).

### Registering it (shown, not applied)

If you wanted this tool available to the live Research Agent, the one-line addition would go in
`build_research_agent()` (`app/agents/research_agent.py`), alongside the other tools:

```python
from app.tools.current_date import CurrentDateTool   # new import

# inside build_research_agent(), wherever `tools = [...]` is built:
tools = [WebSearchTool(build_search_backend(settings)), FetchUrlTool(), CurrentDateTool()]
```

That's it — no other change needed. The system prompt is generated fresh from
`self._registry.describe()` every run, so `current_date` would immediately show up as a third
option the model can choose, with zero changes to `ResearchAgent` itself.

> This snippet is **illustrative only** — it has *not* been applied to `build_research_agent()`.
> Adding a tool to the live agent's default toolset is a real behavior change (it changes what
> every future research run can do), so it's left as a deliberate choice for whoever decides the
> agent actually needs it, rather than a side effect of writing this cookbook doc. The tool file
> and its tests are real and passing either way — you can register it yourself whenever you want it
> live.

---

## Quick checklist before shipping a new tool

- [ ] `name` is short, unique, and matches what the `description` says to call it
- [ ] `description` states the exact argument shape (or explicitly says "no arguments")
- [ ] Every I/O dependency (HTTP client, search backend, clock, file system) is a constructor
      parameter with a sane default — never called as a bare global inside `run()`
- [ ] `run()` returns a `str`, always
- [ ] Tests inject a fake dependency and assert exact output for the happy path
- [ ] No try/except inside `run()` for failures you want `ToolRegistry` to catch and report back to
      the model
- [ ] Registered in the relevant `ToolRegistry([...])` list, only once you actually want it live
