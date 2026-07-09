# Phase 2 — Multi-Provider LLM Layer (Learning Guide)

> **Goal of this phase:** make the agent able to talk to *any* LLM — OpenRouter, NVIDIA, OpenAI,
> Anthropic — where the choice is made entirely by configuration, and a provider outage falls back
> automatically. This is the concrete implementation of your "swap models via ENV" requirement.

---

## 1. What & why

Your requirement: *"It should be able to use multiple models based on the API key and ENV
variables."* Naively, you might scatter `if provider == "openai": ...` checks throughout the code.
That rots fast: every new feature has to know about every provider, and adding Claude means editing
ten files.

The professional answer is **the adapter pattern behind a single interface**:

- Define **one contract** (`LLMProvider`) that says "an LLM can `complete()` a chat."
- Write **one small adapter per vendor** that satisfies the contract and hides that vendor's quirks.
- A **factory** picks the adapter from config at startup.
- The rest of the app depends *only on the contract* — it never imports `openai` or `anthropic`.

Result: adding a provider = **one new file + one line in the factory**. Nothing else changes.
Swapping providers = **one env var**. That's the whole game.

---

## 2. The flow

```
  settings.llm_provider = "anthropic"   (from ENV / .env)
                │
                ▼
     ┌─────────────────────┐
     │  build_llm_provider  │   factory.py — reads config, returns the right adapter
     └──────────┬──────────┘
                │ returns an object that satisfies LLMProvider
                ▼
     ┌─────────────────────┐        wrapped by (optional)
     │   AnthropicProvider  │◄───────────────────────────┐
     │   (or OpenAICompat)  │                             │
     └──────────┬──────────┘                    ┌─────────┴─────────┐
                │ .complete(messages)            │    FallbackLLM     │
                ▼                                │  (retry on the     │
   vendor SDK call (openai / anthropic)          │   fallback model)  │
                │                                └────────────────────┘
                ▼
        ┌───────────────┐
        │  LLMResponse   │   normalized shape: content, model, provider, tokens, finish_reason
        └───────────────┘

  The agent only ever sees:  provider.complete(messages) -> LLMResponse
  It has no idea which vendor answered. That's the point.
```

---

## 3. File-by-file walkthrough

All files live in `app/providers/llm/`.

### `base.py` — the contract and the shared shapes
```python
class ChatMessage(BaseModel):      # role: system|user|assistant, content: str
class LLMResponse(BaseModel):      # content, model, provider, tokens, finish_reason

@runtime_checkable
class LLMProvider(Protocol):
    name: str
    def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None) -> LLMResponse: ...
```
- **`Protocol` = structural typing.** Any class with a `name` and a matching `complete()` *is* an
  `LLMProvider` — no inheritance required. This is Python's version of "define an interface."
- **`@runtime_checkable`** lets `isinstance(x, LLMProvider)` work at runtime (we assert it in a test).
- **`ChatMessage` / `LLMResponse`** are the *normalized* input and output. Every vendor's weird
  response object gets mapped into `LLMResponse`, so callers see one consistent shape.

> **When to use a Protocol:** whenever you'll have multiple interchangeable implementations of the
> same idea (providers, storage backends, notifiers). Depend on the Protocol, not the concretes.

### `openai_compatible.py` — one adapter, three providers
```python
class OpenAICompatibleProvider:
    def __init__(self, *, name, default_model, base_url=None, api_key=None, client=None): ...
    def complete(self, messages, ...) -> LLMResponse: ...
```
- **The insight:** OpenRouter, NVIDIA, and OpenAI all expose the *same* OpenAI-style API. So a single
  adapter, parameterized by `base_url` + `api_key` + `default_model`, covers all three. You just
  point it at a different URL.
- **`client=None` injection:** if a `client` is passed in, it's used directly; otherwise a real
  `openai.OpenAI(...)` is built. Tests pass a **fake client** — that's why the whole suite runs with
  no network and no API keys.
- **Lazy import** (`from openai import OpenAI` *inside* `__init__`): the SDK is only imported when you
  actually build a real client, so tests importing the module don't need the package loaded first.
- `complete()` maps the vendor response → `LLMResponse` (pulls `content`, token usage, `finish_reason`).

### `anthropic_provider.py` — when a vendor is different
```python
class AnthropicProvider:
    def complete(self, messages, ...):
        system = "\n".join(m.content for m in messages if m.role == "system")
        conversation = [... for m in messages if m.role != "system"]
        resp = self._client.messages.create(system=system, messages=conversation,
                                             max_tokens=max_tokens or 1024, ...)
```
- Anthropic's API differs from OpenAI's in two ways this adapter *absorbs* so the caller never has to
  care:
  1. **System prompt is a separate argument**, not a `role: "system"` message. We split it out.
  2. **`max_tokens` is required.** We default it to 1024 when the caller doesn't specify.
- This is the adapter pattern earning its keep: **the ugliness is contained in one file**, and the
  outside world still just calls `complete()` and gets an `LLMResponse`.

### `factory.py` — config → concrete adapter
```python
_OPENAI_COMPATIBLE = {
    "openrouter": ("https://openrouter.ai/api/v1", "openrouter_api_key"),
    "nvidia":     ("https://integrate.api.nvidia.com/v1", "nvidia_api_key"),
    "openai":     (None, "openai_api_key"),
}

def build_llm_provider(settings) -> LLMProvider:
    # picks OpenAICompatibleProvider or AnthropicProvider based on settings.llm_provider
    # raises ValueError for an unknown provider
```
- **This is where "swap via ENV" becomes real.** `LLM_PROVIDER=nvidia` → the factory builds an
  `OpenAICompatibleProvider` pointed at NVIDIA's URL with NVIDIA's key. No caller changes.
- **Fail loud:** an unknown provider raises `ValueError` immediately at startup, rather than failing
  mysteriously on the first API call.
- **Adding a provider later** (say, a local model): add one entry / one branch here + one adapter
  file. That's the entire change.

### `fallback.py` — resilience as a transparent wrapper
```python
class FallbackLLM:                      # also satisfies LLMProvider!
    def complete(self, messages, ...):
        try:
            return self._primary.complete(messages, model=model, ...)
        except Exception:
            if self._fallback_model is None:
                raise
            return self._primary.complete(messages, model=self._fallback_model, ...)
```
- **Why it exists:** LLM APIs rate-limit and go down. `FallbackLLM` retries once on your configured
  `LLM_FALLBACK_MODEL` when the primary call throws.
- **The elegant part:** `FallbackLLM` *is itself* an `LLMProvider` (same `complete()` signature). So
  you wrap your real provider in it and the agent is none the wiser — resilience is added
  **transparently**, like a decorator. This only works *because* everything speaks the same interface.

> **When to use this "wrapper that implements the same interface" trick:** adding retries, caching,
> logging, or metrics around something — without changing the thing or its callers. It's the
> Decorator pattern.

---

## 4. Key concepts (transferable)

| Concept | In one line | When to reach for it |
|---------|-------------|----------------------|
| Protocol / interface | Define a contract; depend on it, not on concretes | Multiple interchangeable impls |
| Adapter pattern | One small class hides a vendor's quirks | Integrating any external API/SDK |
| Factory | Turn config into the right object at startup | "Choose an implementation from config" |
| Dependency injection | Pass collaborators (e.g. `client`) in | To make code testable without real I/O |
| Decorator/wrapper | Same interface, adds behavior around it | Retries, caching, logging, metrics |
| Normalized DTOs | One in/out shape across all vendors | Whenever sources have different formats |

---

## 5. How to run & test it

```bash
# All Phase 2 tests — no network, no API keys required
./.venv/Scripts/python.exe -m pytest tests/providers -v
```

### What each test proves
- `test_base.py` — the DTOs behave and a compliant class *is* an `LLMProvider` (Protocol check).
- `test_openai_compatible.py` — a **fake client** captures the args; we assert the adapter forwards
  `model`/`temperature`/`max_tokens` correctly and maps the response into `LLMResponse`.
- `test_anthropic_provider.py` — proves the system-prompt split and the `max_tokens` default.
- `test_factory.py` — each `LLM_PROVIDER` value selects the right adapter; unknown → `ValueError`.
- `test_fallback.py` — success passes straight through; a primary failure retries on the fallback
  model; with no fallback configured, the error propagates.

### Trying it for real (needs a key)
```env
# .env
LLM_PROVIDER=openrouter
LLM_MODEL=anthropic/claude-sonnet-5
OPENROUTER_API_KEY=sk-or-...
```
```python
from app.config import get_settings
from app.providers.llm.base import ChatMessage
from app.providers.llm.factory import build_llm_provider

llm = build_llm_provider(get_settings())
print(llm.complete([ChatMessage(role="user", content="Say hi in 3 words")]).content)
```
Change `LLM_PROVIDER`/`LLM_MODEL` in `.env`, rerun — different model answers, **zero code changes.**

---

## 6. What's next

Phase 2 gave the agent a voice. **Phase 3** gives it *senses and autonomy*: a `Tool` interface,
`web_search` (native/api/mock) + `fetch_url`, and the ReAct-style loop where the model decides which
tools to call, observes results, and repeats until it has produced a structured `ResearchBrief`.
That's where the `complete()` interface grows tool-calling support — and where it starts to feel
like a real agent.
