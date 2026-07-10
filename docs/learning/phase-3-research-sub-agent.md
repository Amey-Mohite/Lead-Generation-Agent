# Phase 3 — Research Sub-Agent (Learning Guide)

> **Goal of this phase:** build the first *real agent* — an autonomous loop that decides on its
> own to search the web and read pages, then returns a structured `ResearchBrief`. This is where
> the project stops being "a service that calls an LLM" and becomes "an agent."

---

## 1. What & why

An **agent** (as opposed to a single LLM call) is a program where the model **drives a loop**:
it looks at the situation, **chooses an action**, sees the result, and repeats until it's done.
That loop is the whole idea. Everything else (tools, schemas) exists to serve it.

We chose the **ReAct pattern** (Reason + Act): the model alternates between *reasoning* about
what to do and *acting* by calling a tool. We implement it with **plain prompting + JSON** rather
than a vendor's function-calling API, because:

- it works with **every** provider we built (Phase 2), including local models;
- **you build the loop yourself**, so you actually understand how agents work;
- it's **fully testable** with a scripted fake LLM — no keys, no network.

---

## 2. The flow (the ReAct loop)

```
  target = "acme.com"
     │
     ▼
  system prompt  = role + tool descriptions + "reply with ONE JSON object"
  messages       = [system, user("Research this company: acme.com")]
     │
     ▼
  ┌───────────────────────── loop (max_steps) ─────────────────────────┐
  │  resp = llm.complete(messages)                                      │
  │  parsed = extract_json_object(resp.content)                        │
  │                                                                     │
  │   ├─ not JSON?      → append "reply with JSON only", continue       │  (self-correct)
  │   ├─ {"action":..}? → run tool → append "Observation: ...", loop    │  (act)
  │   └─ {"final":..}?  → ResearchBrief(**final)  ─────────────► RETURN  │  (done)
  │                        (invalid? append error, continue)            │
  └─────────────────────────────────────────────────────────────────────┘
     │ (loop exhausted)
     ▼
  raise ResearchError   ← bounded autonomy: never loop forever
```

The model never touches a tool directly. It *asks* for one by name in JSON; the loop executes it
and hands back the result as the next "Observation." That indirection is what keeps the agent
safe and testable.

---

## 3. File-by-file walkthrough

### `app/schemas/research.py` — the structured output
`Contact` and `ResearchBrief` (pydantic). **Why it matters:** the agent's job isn't to *chat*,
it's to produce **data** the rest of the system can use. Making `summary` required means a lazy or
broken model response fails validation loudly instead of flowing garbage into Phase 4. This schema
is the contract between the Research Agent and everything downstream.

### `app/tools/base.py` — `Tool` + `ToolRegistry`
- `Tool` is a Protocol: any object with `name`, `description`, and `run(**kwargs) -> str`.
- `ToolRegistry.describe()` renders the tool list *into the prompt* (so the model knows what it can
  call). `run(name, args)` dispatches to the right tool.
- **Key robustness decision:** `run()` **never raises.** Unknown tool → error string; tool crash →
  error string. The agent reads that string and can try again. *A crashing tool must not kill the
  loop.*

### `app/tools/search/` — pluggable search (for `mock` / `api` modes)
Same interface-and-factory pattern as the LLM layer, applied to search:
- `base.py` — `SearchResult` + `SearchBackend` protocol.
- `mock.py` — deterministic **fake** results (`"Mock snippet 1 about <query>"`). Proves the loop's
  plumbing works, but is **not real research** — don't mistake a clean mock run for a real one.
- `tavily.py` — real web search via httpx (injectable client for testing).
- `factory.py` — `build_search_backend(settings)`: `api`→Tavily, else (`mock`/`native`)→Mock.

> **Why a separate backend layer?** So the `web_search` tool doesn't care whether results come
> from a mock or Tavily. Swap by config, not code.

### `app/providers/llm/online.py` — `native` mode (real search, one key)
`native` doesn't go through the tool loop at all — it's a different mechanism. The LLM *provider
itself* runs live web search and grounds its answer, before your code ever sees a response. So
`OnlineSearchLLM` **wraps the LLM provider** (same decorator idea as `FallbackLLM`) — but unlike
`FallbackLLM`, it can't apply one trick to every vendor, because **each vendor implements native
search completely differently**. This turned out to be a genuinely important lesson: an early
version of this file assumed only OpenRouter had this capability and hard-coded that as a
constraint. That was wrong — it was just the one mechanism that had been implemented, not a fact
about the ecosystem. Checking each vendor's actual docs (not memory) turned up two more real,
supported mechanisms, so `OnlineSearchLLM` now **dispatches on `primary.name`**:

| Provider | Mechanism | Where it's implemented |
|----------|-----------|-------------------------|
| **OpenRouter** | Append `:online` to the model id | `OnlineSearchLLM` rewrites the model string itself |
| **Anthropic** | A server-side `web_search_20260209` tool declared in the `tools` param of the Messages API | `AnthropicProvider.complete(..., tools=[...])` — the provider gained an optional `tools` kwarg (default `None`, fully backward-compatible) |
| **OpenAI** | A server-side `web_search` tool, but **only on the Responses API** — a different endpoint than Chat Completions, with a different request/response shape (`input`/`instructions` instead of `messages`, `output_text` instead of `choices[0].message.content`) | `OpenAICompatibleProvider.complete_native_search(...)` — a *separate method*, not a variant of `complete()`, because the two OpenAI endpoints genuinely don't share a shape |
| **NVIDIA NIM** | *(none)* | Verified against NVIDIA's own docs: NIM only exposes generic tool-calling, no built-in search grounding. `native` mode isn't offered here — use `api` instead. |

`build_research_agent()` branches on `research_search_mode`: in `native` mode it wraps the LLM in
`OnlineSearchLLM` and **drops the `web_search` tool** from the registry (there's nothing for it to
do — the model already searched by the time it replies), keeping only `fetch_url` for deep-diving
a specific URL.

> **Verify vendor capabilities against live docs, not training memory.** APIs change constantly,
> and "provider X doesn't support Y" is a claim, not a default — get it wrong and you under-build
> (missing a real capability) just as easily as you can overclaim one. Before writing the OpenAI
> Responses API code above, the exact parameter names (`max_output_tokens`, `instructions`,
> `output_text`, `usage.input_tokens`) were confirmed against the **actual installed SDK's type
> signatures** (`inspect.signature(...)`, `Model.model_fields`), not recalled — docs drift, but the
> library you're about to import doesn't lie.

Remaining known gap: **citations aren't captured into `sources` yet.** Both OpenRouter and
OpenAI return grounding citations as structured metadata (`annotations` / `url_citation` blocks)
separate from the answer text, which none of our providers currently parse — so `sources` in the
final brief depends on the model choosing to list URLs in its own prose. Wiring that up is a good
follow-up enhancement.

| Mode | Real web search? | Needs | Providers |
|------|:-:|-------|-----------|
| `mock` | ❌ (fake) | nothing | any |
| `api` | ✅ | `SEARCH_API_KEY` (Tavily) | any (goes through our own `web_search` tool) |
| `native` | ✅ | nothing extra | openrouter, anthropic, openai (**not** nvidia) |

### `app/tools/web_search.py` & `fetch_url.py` — the agent's hands
- `WebSearchTool` wraps a backend and **formats** results into readable text for the model.
- `FetchUrlTool` downloads a page and **strips it to readable text** (removes `<script>`/`<style>`),
  truncated so we don't blow the context window. The `fetcher` is **injectable** — tests pass a
  lambda returning canned HTML, so no network.

### `app/agents/json_utils.py` — tolerant parsing
`extract_json_object()` finds the first balanced `{...}` in the model's reply. **Why:** models love
to wrap JSON in ```json fences or add "Sure! Here you go:". A brittle `json.loads(whole_reply)`
would fail constantly. This scans for the first balanced object and parses just that.

> **When to use:** any time you ask an LLM for JSON without a strict structured-output mode. Assume
> the response is *dirty* and parse defensively.

### `app/agents/research_agent.py` — the loop
- `ResearchAgent.run(target)` builds the system prompt (injecting tool descriptions + `max_steps`),
  seeds the conversation, and runs the loop described in §2.
- Three production behaviors to notice:
  1. **Bounded autonomy** — the `for _ in range(max_steps)` cap. Without it, a confused model could
     loop (and bill you) forever. It raises `ResearchError` instead.
  2. **Self-correction** — a non-JSON reply or an invalid `final` doesn't crash; it appends a
     corrective message and gives the model another turn.
  3. **Grounding** — the prompt says "base every fact on tool observations; do not invent." Tool
     results are fed back as "Observation:" messages so the model answers from evidence.
- `build_research_agent(settings)` assembles the whole thing from config: provider (+ fallback) +
  search backend + tools. One function, and you have a working agent driven entirely by ENV.

---

## 4. Key concepts (transferable)

| Concept | In one line | When to reach for it |
|---------|-------------|----------------------|
| ReAct loop | Model reasons, acts via a tool, observes, repeats | Any autonomous/agentic task |
| Tool registry | Decouple "what tools exist" from "how they're used" | Any tool-using agent |
| Bounded autonomy | Hard cap on steps/cost | Every agent loop, always |
| Tolerant JSON parsing | Assume LLM output is dirty; parse defensively | Any "LLM returns JSON" flow |
| Self-correction | Feed errors back instead of crashing | Robust agent loops |
| Injectable backends/fetchers | Pass I/O dependencies in | To test without network/keys |
| Structured output schema | Validate the agent's result into data | When the result feeds other code |
| Provider-level vs tool-level search | Some "search" happens inside the LLM call, not via a tool | Whenever the provider offers built-in grounding |

---

## 5. How to run & test it

```bash
# All Phase 3 tests — no network, no keys (mock search + scripted fake LLM)
./.venv/Scripts/python.exe -m pytest tests/schemas tests/tools tests/agents tests/providers -v
```

### What the tests prove
- `test_research.py` — the brief validates; `summary` is required.
- `test_tool_registry.py` — dispatch works; unknown tool returns an error string (no crash).
- `test_search_mock.py` — mock backend returns results; factory picks mock for `mock`/`native`.
- `test_web_search.py` / `test_fetch_url.py` — tools format results / strip HTML & truncate.
- `test_json_utils.py` — parses plain JSON, fenced JSON with prose, and returns None on garbage.
- `test_research_agent.py` — **the loop**: (a) search→final happy path, (b) recovers from a bad
  turn, (c) raises when `max_steps` is exceeded.
- `test_build_research_agent.py` — assembles a working agent from settings alone; proves `native`
  mode wires in `OnlineSearchLLM` (dropping the `web_search` tool) for **all three** supported
  providers — openrouter, anthropic, and openai — not just openrouter.
- `test_anthropic_provider.py` — the new `tools` kwarg is omitted by default (existing calls
  unaffected) and passed through verbatim when given.
- `test_openai_compatible.py` — `complete_native_search()` calls the Responses API with the right
  shape (`instructions`, `input`, `tools=[{"type":"web_search"}]`) and rejects any provider name
  other than `openai`.
- `test_online.py` — `OnlineSearchLLM` dispatches correctly per provider (OpenRouter's `:online`
  suffix, Anthropic's `tools` passthrough, OpenAI's `complete_native_search`) and rejects a
  genuinely unsupported provider (NVIDIA) with a clear error.

### Trying it for real
```bash
./.venv/Scripts/python.exe scripts/try_research.py "stripe.com"
```
This script auto-detects: with no LLM key it runs an offline scripted demo; with a key, it builds
a real agent via `build_research_agent()` (so it automatically respects whatever
`RESEARCH_SEARCH_MODE` you set) and prints a live trace of every turn. Three ways to get *real*
company research:

```env
# Option A: native — one key only, OpenRouter's live web search
LLM_PROVIDER=openrouter
LLM_MODEL=anthropic/claude-3.5-haiku
OPENROUTER_API_KEY=sk-or-...
RESEARCH_SEARCH_MODE=native

# Option B: api — explicit search tool via Tavily (works with any LLM provider)
RESEARCH_SEARCH_MODE=api
SEARCH_PROVIDER=tavily
SEARCH_API_KEY=tvly-...

# Option C: mock — zero keys, but NOT real research (fake snippets only)
RESEARCH_SEARCH_MODE=mock
```

---

## 6. Understanding a real run — FAQ

Building the loop is one thing; watching it run against a real target and understanding *why* it
did what it did is another. This section works through the questions that came up debugging a real
`stripe.com` run — they're worth having clear answers to before building Phase 4 on top of this.

### 6.1 Does `max_steps` always run that many times?

No. Look at the loop again:

```python
for _ in range(self._max_steps):        # a hard ceiling, not a fixed count
    resp = self._llm.complete(messages)
    ...
    if "final" in parsed:
        return ResearchBrief(**parsed["final"])   # returns IMMEDIATELY — loop stops here
    ...
raise ResearchError(...)                 # only reached if every iteration is exhausted
```

- The loop **returns the instant** the model emits a valid `{"final": {...}}` — it could finish in
  2 calls or 6, entirely depending on how quickly the model decides it has enough.
- `max_steps` is only ever a **ceiling**: if the model never successfully finalizes, the `for` loop
  exhausts and `ResearchError` is raised instead of looping forever. That's the actual purpose — a
  cost/safety cap against a confused model looping indefinitely, not a target number of calls.
- The model is *told* its budget: the system prompt literally injects `{max_steps}` into the text
  (`"Finish within {max_steps} steps."`). That's why models tend to use their **last available
  turn** to finalize rather than running over — they're pacing themselves against a deadline
  they were explicitly given.
- **Practical risk:** a run that uses all `N` steps (a real Stripe run hit exactly 6/6) has zero
  margin left. If two of those steps get burned on dead ends (e.g. two `403`s), there's no room to
  recover from a third. Bump `max_steps` up (8–10) for real usage against sites that might block
  requests.

### 6.2 How does the model decide what to search or which URLs to fetch?

Nothing in the system prompt says "search for founders" or "try Wikipedia." Those decisions are
entirely the model's own reasoning, and they come from two sources:

1. **The model's own trained knowledge.** LLMs have seen enormous amounts of text about how
   companies get researched and which resources (Wikipedia, Crunchbase, LinkedIn) are typically
   useful. For a well-known company, the model may already "know" a plausible URL — e.g. guessing
   `en.wikipedia.org/wiki/Stripe,_Inc.` without that URL ever appearing in a search result —
   because it has seen that exact URL pattern many times before.
2. **The shape of the `final` output schema acts as an implicit checklist.** Because the schema
   requires `contacts` and `key_facts`, the model infers it should go looking for founder names and
   company facts — *we never said "find the founders,"* the schema silently steered the plan.
   Designing your output format is itself a form of task instruction.

Mechanically, this works because **each call sees the entire accumulated conversation**, not just
the system prompt:

```python
messages.append(ChatMessage(role="assistant", content=resp.content))               # the model's own prior reply
messages.append(ChatMessage(role="user", content=f"Observation:\n{observation}"))  # what a tool returned
```

By the time the model is deciding its 5th action, it has already read every prior search snippet,
every fetch result, and every one of its own past decisions. It reasons over that accumulated
context and adjusts — e.g. abandoning external sites after two `403`s and trying the company's own
site instead. This *is* the ReAct pattern: Reason → Act → Observe → repeat, with the plan evolving
turn by turn based on what was actually learned, not a fixed script.

> **Non-determinism note:** `complete()` defaults to `temperature=0.7`. Running the same target
> twice will likely produce a *different* sequence of searches and URLs — the model is sampling a
> reasonable research path each time, not executing a deterministic algorithm.

### 6.3 Message roles — when to use `system`, `user`, and `assistant`

| Role | Meaning | Set how often |
|------|---------|----------------|
| `system` | The persistent rules of the conversation — tool contract, output format, constraints. The model's "operating instructions." | Once, at the start. Should not change mid-run. |
| `user` | Anything from *outside* the model — the actual task, or (in our design) data being handed *to* the model. | Every time something new needs the model's attention. |
| `assistant` | Only the model's **own past replies** — never written by your code. Exists so the model can see what it already said. | Appended automatically after every model response. |

In `ResearchAgent.run()`:
```python
messages = [
    ChatMessage(role="system", content=system),                              # once
    ChatMessage(role="user", content=f"Research this company: {target}"),    # the task
]
...
messages.append(ChatMessage(role="assistant", content=resp.content))                # the model's own reply
messages.append(ChatMessage(role="user", content=f"Observation:\n{observation}"))   # tool result
```

**A deliberate simplification worth knowing:** tool observations are sent back as `role="user"`,
not a dedicated "tool" role. Real chat APIs are more precise here — OpenAI's Chat Completions has
an actual `role: "tool"` (linked via `tool_call_id`), and Anthropic's Messages API wraps results in
a `tool_result` content block. Our `ChatMessage` schema only supports the three basic roles because
we chose the **prompt-based ReAct loop** (Phase 3's design decision) over native function-calling —
so we approximate: anything that isn't the model's own words goes in as `user`, and the
`"Observation:\n..."` prefix is just a text convention the model learns to recognize, not a
structural guarantee.

**Two practical rules:**
- Never write something as `assistant` yourself — it must be an exact copy of what the model
  actually said, or you corrupt its memory of its own decisions.
- Keep `system` stable across a run — changing it mid-conversation both confuses the model and
  breaks prompt caching (a cost optimization relevant in later, higher-volume phases).

### 6.4 Why "only ONE action per turn" — and how that differs from "only one tool"

The system prompt says *"Only ONE action per turn."* It's easy to misread this as "the model can
only use one kind of tool." It doesn't mean that at all — these are two independent constraints:

| Concept | Restriction | Evidence from a real run |
|---|---|---|
| How many tools can be registered | Unlimited — `ToolRegistry` just holds a list | We registered 2 (`web_search`, `fetch_url`); could be 10 |
| Which tool the model picks, per turn | Fully free choice, every turn | A real run picked `web_search` → `fetch_url` → `fetch_url` → `fetch_url` → `web_search` → finalize, switching freely |
| How many tools it can call *in one turn* | Exactly one | The actual restriction — one JSON action object per reply |

Two reasons for the one-action-per-turn restriction:

1. **Our parser only extracts one JSON object per reply** (`extract_json_object()` grabs the first
   balanced `{...}`). Supporting multiple actions per turn would need a different schema (a *list*
   of actions), parallel execution, and merged observations — meaningfully more complexity for a
   teaching-scale project.
2. **Sequential, one-at-a-time is what lets the model *adapt*.** A real run illustrates this
   precisely:
   ```
   Call #2: fetch_url(wikipedia)  → 403 Forbidden
   Call #3: fetch_url(crunchbase) → 403 Forbidden   ← chosen AFTER seeing wikipedia fail
   Call #4: fetch_url(stripe.com) → 200 OK          ← chosen AFTER seeing crunchbase fail
   ```
   If the model had fired all three fetches in one batched turn (before seeing any result), it
   couldn't have learned "external sites are blocking me" and pivoted — it would've just burned
   three tool calls blind. One-at-a-time is what makes this a genuine ReAct loop rather than a
   batch of guesses.

**Worth knowing:** real provider APIs *do* support "parallel tool use" — multiple tool calls in one
reply, executed concurrently, when the actions are genuinely independent. We didn't use it here
because (a) it only exists in native function-calling, which Phase 3 deliberately avoided, and
(b) for dependent, exploratory research steps like these, sequential adaptation is actually the
*better* strategy, not just the easier one to build.

### 6.5 `native` vs `api` search mode — a real comparison, and the trust trade-off

Running the same target (`stripe.com`) in both modes produces genuinely different — and
differently *trustworthy* — results.

**In `api` mode**, every fact traces back to an explicit line in our own `messages` log:
```
Observation:
1. Stripe Inc Company Profile - Overview - GlobalData
   https://www.globaldata.com/company-profile/stripe-inc
   ...
```
Every claim in the final brief can be checked against a real, logged `Observation:` — fully
auditable.

**In `native` mode**, the final brief cited facts and URLs — `stripe.com/payments`, a 2025
newsroom update, a specific annual-letter PDF, precise YoY growth percentages — that **never
appeared in any observation we logged.** Our own trace showed only two `fetch_url` attempts (one
`403`, one successful homepage fetch), yet the final answer contained far more than that homepage
page could have supplied.

**Why:** in `native` mode, `OnlineSearchLLM` makes the *provider itself* (OpenRouter's `:online`
model) run its own web search internally — entirely separate from, and invisible to, our own
`fetch_url` tool loop. The model found real pages on its own, outside anything we can see or log.

**The honest trade-off:**

| | `api` mode | `native` mode |
|---|---|---|
| Richness | Limited to what our own tools fetch | Can be much richer — the provider's own search sees more |
| Auditability | Every fact traces to a logged `Observation:` | Facts can come from a search step we cannot see or verify |
| Enforcing "don't invent" | Checkable — every claim maps to an observation | Effectively an honor system — we can't distinguish genuine grounding from partially blended recall |

Richer isn't automatically more trustworthy for a system whose output feeds into qualification and
outreach decisions (as this one eventually will, in Phase 4+). This is exactly the "citations
aren't captured yet" limitation flagged in §3 above — a good follow-up enhancement would be parsing
the provider's citation `annotations` metadata to recover *some* transparency even in `native` mode.

### 6.6 The `TracingLLM` decorator — a debugging tool, not part of production

`scripts/try_research.py` defines a small decorator (same pattern as `FallbackLLM` and
`OnlineSearchLLM`) purely to make the loop's internals visible while learning or debugging:

```python
class TracingLLM:
    def __init__(self, inner):
        self._inner = inner
        self.name = f"tracing({inner.name})"
        self._turn = 0

    def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
        self._turn += 1
        print(f"\n=== LLM call #{self._turn} — latest message to model ===")
        print(messages[-1].content[:600])
        resp = self._inner.complete(messages, model=model, temperature=temperature, max_tokens=max_tokens)
        print(f"\n--- model replied ---\n{resp.content[:600]}\n")
        return resp
```

**Installed by reaching directly into the already-built agent:**
```python
agent = build_research_agent(settings)
agent._llm = TracingLLM(agent._llm)   # swap in the wrapper after construction
```

**When it fires:** exactly once per iteration of the ReAct loop — the very first line of the loop
body in `ResearchAgent.run()`:
```python
for _ in range(self._max_steps):
    resp = self._llm.complete(messages)   # ← this line, every iteration
```
That's the only point in the whole loop that needs the model at all — parsing, tool execution, and
schema validation are all deterministic Python code in between calls. The loop stops calling it the
instant a valid `{"final": {...}}` is returned (immediate `return`), or once `max_steps` is
exhausted (falls through to `raise ResearchError`).

**Is `self._llm.complete(messages)` really hitting the real model?** Yes — but through a chain of
transparent wrappers, not directly. For a `native`-mode OpenRouter run, `agent._llm` after wrapping
looks like:
```
TracingLLM                     ← prints, then delegates
  └─ FallbackLLM                ← retries on the fallback model if the primary errors
       └─ OnlineSearchLLM       ← appends ":online" to the model id
            └─ OpenAICompatibleProvider   ← THE ONLY layer that makes a real HTTP call
```
The actual network request happens one layer down, inside `OpenAICompatibleProvider.complete()`:
```python
resp = self._client.chat.completions.create(model=..., messages=..., ...)
```
Every wrapper above it is transparent — it observes, retries, or rewrites the *request*, but never
fabricates the *response*. The `resp` that flows back up through all four layers to
`ResearchAgent` is the genuine model output. This is the same lesson as `FallbackLLM` and
`OnlineSearchLLM`: because every layer in the chain honors the identical `complete()` contract, you
can stack arbitrary cross-cutting behavior (retries, native search, logging) around the real
provider without the caller ever needing to know.

---

## 7. What's next

The Research Agent produces facts. **Phase 4 — the Lead Orchestrator Agent** — consumes the
`ResearchBrief`, **qualifies** the company against an Ideal Customer Profile (a score + written
reasoning), and **drafts** personalized outreach, emitting a validated `Lead`. That's the
"judgment" layer on top of the "senses" we built here.
