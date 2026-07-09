# Phase 2: Multi-Provider LLM Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the provider abstraction that lets the agent talk to any LLM — OpenRouter, NVIDIA, OpenAI, Anthropic — chosen entirely by ENV config, with an automatic fallback model on failure. This is the concrete implementation of the "swap models via ENV" requirement.

**Architecture:** A `LLMProvider` Protocol defines one method, `complete()`. Because OpenRouter, NVIDIA, and OpenAI all speak the OpenAI-compatible API, a single `OpenAICompatibleProvider` (parameterized by base URL + key + default model) covers all three; Anthropic gets its own thin adapter. A `build_llm_provider(settings)` factory selects the implementation from config. A `FallbackLLM` wrapper retries on the configured fallback model when the primary call fails. Every provider takes an **injectable client**, so tests run with fakes — no network, no keys.

**Tech Stack:** Python 3.12, pydantic, `openai` SDK (covers OpenRouter/NVIDIA/OpenAI), `anthropic` SDK, pytest.

## Global Constraints

- **Python:** 3.12+.
- **No network in tests:** every provider accepts an injectable `client`; tests pass a fake. No test may make a real API call.
- **Config-driven selection:** provider choice comes only from `Settings.llm_provider`; adding a provider = new adapter + one `if` branch, no changes to agent code.
- **Interface stability:** all providers and the fallback wrapper implement the exact same `complete()` signature and return an `LLMResponse`.
- **Secrets:** API keys come from `Settings` (ENV) only; never hard-coded, never logged.
- **Every task ends** with: tests green, one commit.

---

### Task 1: LLM schemas + `LLMProvider` protocol

**Files:**
- Create: `app/providers/__init__.py` (empty)
- Create: `app/providers/llm/__init__.py` (empty)
- Create: `app/providers/llm/base.py`
- Test: `tests/providers/__init__.py` (empty), `tests/providers/test_base.py`

**Interfaces:**
- Produces:
  - `ChatMessage(BaseModel)` — `role: Literal["system","user","assistant"]`, `content: str`.
  - `LLMResponse(BaseModel)` — `content: str`, `model: str`, `provider: str`, `prompt_tokens: int = 0`, `completion_tokens: int = 0`, `finish_reason: str | None = None`.
  - `LLMProvider(Protocol)` — attribute `name: str`; method `complete(self, messages: list[ChatMessage], *, model: str | None = None, temperature: float = 0.7, max_tokens: int | None = None) -> LLMResponse`.

- [ ] **Step 1: Write the failing test** — `tests/providers/test_base.py`

```python
from app.providers.llm.base import ChatMessage, LLMProvider, LLMResponse


def test_chat_message_roundtrip():
    m = ChatMessage(role="user", content="hi")
    assert m.model_dump() == {"role": "user", "content": "hi"}


def test_llm_response_defaults():
    r = LLMResponse(content="hello", model="m", provider="p")
    assert r.prompt_tokens == 0
    assert r.completion_tokens == 0
    assert r.finish_reason is None


def test_protocol_is_runtime_checkable():
    class Dummy:
        name = "dummy"

        def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
            return LLMResponse(content="x", model="m", provider="dummy")

    assert isinstance(Dummy(), LLMProvider)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/providers/test_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.providers'`.

- [ ] **Step 3: Create the empty package files**

Create empty (0-byte): `app/providers/__init__.py`, `app/providers/llm/__init__.py`, `tests/providers/__init__.py`.

- [ ] **Step 4: Create `app/providers/llm/base.py`**

```python
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMResponse(BaseModel):
    content: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str | None = None


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/providers/test_base.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add app/providers/ tests/providers/
git commit -m "feat: LLM schemas and LLMProvider protocol"
```

---

### Task 2: OpenAI-compatible provider (OpenRouter / NVIDIA / OpenAI)

**Files:**
- Modify: `pyproject.toml` (add `openai>=1.40` to `dependencies`)
- Create: `app/providers/llm/openai_compatible.py`
- Test: `tests/providers/test_openai_compatible.py`

**Interfaces:**
- Consumes: `ChatMessage`, `LLMResponse` from `base`.
- Produces: `OpenAICompatibleProvider(*, name: str, default_model: str, base_url: str | None = None, api_key: str | None = None, client=None)` implementing `LLMProvider`. When `client` is provided it is used as-is (for tests); otherwise a real `openai.OpenAI` client is built from `base_url`/`api_key`.

- [ ] **Step 1: Add dependency** — in `pyproject.toml`, change the `dependencies` list to include `"openai>=1.40"`:

```toml
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "pydantic-settings>=2.4",
    "sqlalchemy>=2.0",
    "psycopg[binary]>=3.2",
    "openai>=1.40",
]
```

- [ ] **Step 2: Install the new dependency**

Run: `./.venv/Scripts/python.exe -m pip install -q -e "."`
Expected: installs `openai` and its deps; exit 0.

- [ ] **Step 3: Write the failing test** — `tests/providers/test_openai_compatible.py`

```python
from types import SimpleNamespace

from app.providers.llm.base import ChatMessage
from app.providers.llm.openai_compatible import OpenAICompatibleProvider


class _FakeCompletions:
    def __init__(self, captured):
        self._captured = captured

    def create(self, **kwargs):
        self._captured.update(kwargs)
        return SimpleNamespace(
            model="resolved-model",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="hello there"),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
        )


class _FakeClient:
    def __init__(self, captured):
        self.chat = SimpleNamespace(completions=_FakeCompletions(captured))


def test_complete_maps_response_and_passes_args():
    captured: dict = {}
    provider = OpenAICompatibleProvider(
        name="openrouter", default_model="default-model", client=_FakeClient(captured)
    )
    resp = provider.complete(
        [ChatMessage(role="user", content="hi")], temperature=0.2, max_tokens=100
    )

    assert resp.content == "hello there"
    assert resp.provider == "openrouter"
    assert resp.model == "resolved-model"
    assert resp.prompt_tokens == 11
    assert resp.completion_tokens == 7
    assert resp.finish_reason == "stop"
    # default model used when none passed; args forwarded
    assert captured["model"] == "default-model"
    assert captured["temperature"] == 0.2
    assert captured["max_tokens"] == 100
    assert captured["messages"] == [{"role": "user", "content": "hi"}]


def test_explicit_model_overrides_default():
    captured: dict = {}
    provider = OpenAICompatibleProvider(
        name="nvidia", default_model="default-model", client=_FakeClient(captured)
    )
    provider.complete([ChatMessage(role="user", content="hi")], model="override")
    assert captured["model"] == "override"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/providers/test_openai_compatible.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.providers.llm.openai_compatible'`.

- [ ] **Step 5: Create `app/providers/llm/openai_compatible.py`**

```python
from app.providers.llm.base import ChatMessage, LLMResponse


class OpenAICompatibleProvider:
    """LLM provider for any OpenAI-compatible API (OpenRouter, NVIDIA, OpenAI)."""

    def __init__(
        self,
        *,
        name: str,
        default_model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        client=None,
    ) -> None:
        self.name = name
        self.default_model = default_model
        if client is not None:
            self._client = client
        else:
            from openai import OpenAI

            self._client = OpenAI(base_url=base_url, api_key=api_key)

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        resp = self._client.chat.completions.create(
            model=model or self.default_model,
            messages=[m.model_dump() for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        choice = resp.choices[0]
        usage = resp.usage
        return LLMResponse(
            content=choice.message.content or "",
            model=resp.model,
            provider=self.name,
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
            finish_reason=choice.finish_reason,
        )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/providers/test_openai_compatible.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml app/providers/llm/openai_compatible.py tests/providers/test_openai_compatible.py
git commit -m "feat: OpenAI-compatible LLM provider (OpenRouter/NVIDIA/OpenAI)"
```

---

### Task 3: Anthropic provider

**Files:**
- Modify: `pyproject.toml` (add `anthropic>=0.39` to `dependencies`)
- Create: `app/providers/llm/anthropic_provider.py`
- Test: `tests/providers/test_anthropic_provider.py`

**Interfaces:**
- Consumes: `ChatMessage`, `LLMResponse`.
- Produces: `AnthropicProvider(*, default_model: str, api_key: str | None = None, client=None)` implementing `LLMProvider`. Splits `system` messages out of the conversation (Anthropic takes `system` as a separate arg); defaults `max_tokens` to 1024 when not provided (Anthropic requires it).

- [ ] **Step 1: Add dependency** — add `"anthropic>=0.39"` to `dependencies` in `pyproject.toml`.

- [ ] **Step 2: Install**

Run: `./.venv/Scripts/python.exe -m pip install -q -e "."`
Expected: installs `anthropic`; exit 0.

- [ ] **Step 3: Write the failing test** — `tests/providers/test_anthropic_provider.py`

```python
from types import SimpleNamespace

from app.providers.llm.anthropic_provider import AnthropicProvider
from app.providers.llm.base import ChatMessage


class _FakeMessages:
    def __init__(self, captured):
        self._captured = captured

    def create(self, **kwargs):
        self._captured.update(kwargs)
        return SimpleNamespace(
            model="claude-x",
            content=[SimpleNamespace(type="text", text="hi from claude")],
            usage=SimpleNamespace(input_tokens=5, output_tokens=3),
            stop_reason="end_turn",
        )


class _FakeClient:
    def __init__(self, captured):
        self.messages = _FakeMessages(captured)


def test_complete_splits_system_and_maps_usage():
    captured: dict = {}
    provider = AnthropicProvider(default_model="claude-default", client=_FakeClient(captured))
    resp = provider.complete(
        [
            ChatMessage(role="system", content="be brief"),
            ChatMessage(role="user", content="hello"),
        ]
    )

    assert resp.content == "hi from claude"
    assert resp.provider == "anthropic"
    assert resp.prompt_tokens == 5
    assert resp.completion_tokens == 3
    assert resp.finish_reason == "end_turn"
    # system pulled out; only non-system messages in conversation
    assert captured["system"] == "be brief"
    assert captured["messages"] == [{"role": "user", "content": "hello"}]
    # anthropic requires max_tokens -> default applied
    assert captured["max_tokens"] == 1024
    assert captured["model"] == "claude-default"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/providers/test_anthropic_provider.py -v`
Expected: FAIL — module not found.

- [ ] **Step 5: Create `app/providers/llm/anthropic_provider.py`**

```python
from app.providers.llm.base import ChatMessage, LLMResponse


class AnthropicProvider:
    """LLM provider for Anthropic's native Messages API."""

    def __init__(
        self,
        *,
        default_model: str,
        api_key: str | None = None,
        client=None,
    ) -> None:
        self.name = "anthropic"
        self.default_model = default_model
        if client is not None:
            self._client = client
        else:
            from anthropic import Anthropic

            self._client = Anthropic(api_key=api_key)

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        system = "\n".join(m.content for m in messages if m.role == "system")
        conversation = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role != "system"
        ]
        resp = self._client.messages.create(
            model=model or self.default_model,
            system=system,
            messages=conversation,
            max_tokens=max_tokens or 1024,
            temperature=temperature,
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        return LLMResponse(
            content=text,
            model=resp.model,
            provider=self.name,
            prompt_tokens=getattr(resp.usage, "input_tokens", 0),
            completion_tokens=getattr(resp.usage, "output_tokens", 0),
            finish_reason=resp.stop_reason,
        )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/providers/test_anthropic_provider.py -v`
Expected: PASS (1 passed).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml app/providers/llm/anthropic_provider.py tests/providers/test_anthropic_provider.py
git commit -m "feat: Anthropic LLM provider (native Messages API)"
```

---

### Task 4: Provider factory (select by config)

**Files:**
- Create: `app/providers/llm/factory.py`
- Test: `tests/providers/test_factory.py`

**Interfaces:**
- Consumes: `Settings`, `OpenAICompatibleProvider`, `AnthropicProvider`.
- Produces: `build_llm_provider(settings: Settings) -> LLMProvider`. Maps `settings.llm_provider`:
  `openrouter` → base `https://openrouter.ai/api/v1`, key `openrouter_api_key`;
  `nvidia` → base `https://integrate.api.nvidia.com/v1`, key `nvidia_api_key`;
  `openai` → default base, key `openai_api_key`;
  `anthropic` → `AnthropicProvider`, key `anthropic_api_key`.
  Unknown → `ValueError`. All use `settings.llm_model` as `default_model`.

- [ ] **Step 1: Write the failing test** — `tests/providers/test_factory.py`

```python
import pytest

from app.config import Settings
from app.providers.llm.anthropic_provider import AnthropicProvider
from app.providers.llm.factory import build_llm_provider
from app.providers.llm.openai_compatible import OpenAICompatibleProvider


def _settings(**over):
    base = dict(
        llm_model="test-model",
        openrouter_api_key="or-key",
        nvidia_api_key="nv-key",
        openai_api_key="oa-key",
        anthropic_api_key="an-key",
    )
    base.update(over)
    return Settings(_env_file=None, **base)


def test_openrouter_selected():
    p = build_llm_provider(_settings(llm_provider="openrouter"))
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.name == "openrouter"
    assert p.default_model == "test-model"


def test_nvidia_selected():
    p = build_llm_provider(_settings(llm_provider="nvidia"))
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.name == "nvidia"


def test_anthropic_selected():
    p = build_llm_provider(_settings(llm_provider="anthropic"))
    assert isinstance(p, AnthropicProvider)
    assert p.name == "anthropic"


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        build_llm_provider(_settings(llm_provider="does-not-exist"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/providers/test_factory.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `app/providers/llm/factory.py`**

```python
from app.config import Settings
from app.providers.llm.anthropic_provider import AnthropicProvider
from app.providers.llm.base import LLMProvider
from app.providers.llm.openai_compatible import OpenAICompatibleProvider

_OPENAI_COMPATIBLE = {
    "openrouter": ("https://openrouter.ai/api/v1", "openrouter_api_key"),
    "nvidia": ("https://integrate.api.nvidia.com/v1", "nvidia_api_key"),
    "openai": (None, "openai_api_key"),
}


def build_llm_provider(settings: Settings) -> LLMProvider:
    provider = settings.llm_provider.lower()

    if provider in _OPENAI_COMPATIBLE:
        base_url, key_attr = _OPENAI_COMPATIBLE[provider]
        return OpenAICompatibleProvider(
            name=provider,
            default_model=settings.llm_model,
            base_url=base_url,
            api_key=getattr(settings, key_attr),
        )

    if provider == "anthropic":
        return AnthropicProvider(
            default_model=settings.llm_model,
            api_key=settings.anthropic_api_key,
        )

    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/providers/test_factory.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/providers/llm/factory.py tests/providers/test_factory.py
git commit -m "feat: LLM provider factory (config-driven selection)"
```

---

### Task 5: Fallback router + README update

**Files:**
- Create: `app/providers/llm/fallback.py`
- Test: `tests/providers/test_fallback.py`
- Modify: `README.md` (mark Phase 2 done)

**Interfaces:**
- Consumes: `LLMProvider`, `LLMResponse`, `ChatMessage`.
- Produces: `FallbackLLM(primary: LLMProvider, fallback_model: str | None)` implementing `LLMProvider` (`name = f"{primary.name}+fallback"`). `complete()` calls the primary; on any `Exception`, if `fallback_model` is set it retries once with `model=fallback_model`; otherwise re-raises.

- [ ] **Step 1: Write the failing test** — `tests/providers/test_fallback.py`

```python
import pytest

from app.providers.llm.base import ChatMessage, LLMResponse
from app.providers.llm.fallback import FallbackLLM


class _Primary:
    name = "primary"

    def __init__(self, fail_first: bool):
        self.fail_first = fail_first
        self.calls: list[str | None] = []

    def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
        self.calls.append(model)
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("primary boom")
        return LLMResponse(content="ok", model=model or "primary-model", provider="primary")


def _msgs():
    return [ChatMessage(role="user", content="hi")]


def test_passthrough_on_success():
    p = _Primary(fail_first=False)
    llm = FallbackLLM(p, fallback_model="fb-model")
    resp = llm.complete(_msgs())
    assert resp.content == "ok"
    assert p.calls == [None]  # fallback never used


def test_retries_with_fallback_model_on_failure():
    p = _Primary(fail_first=True)
    llm = FallbackLLM(p, fallback_model="fb-model")
    resp = llm.complete(_msgs())
    assert resp.content == "ok"
    assert p.calls == [None, "fb-model"]  # first default, then fallback


def test_reraises_when_no_fallback():
    p = _Primary(fail_first=True)
    llm = FallbackLLM(p, fallback_model=None)
    with pytest.raises(RuntimeError):
        llm.complete(_msgs())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/providers/test_fallback.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `app/providers/llm/fallback.py`**

```python
from app.providers.llm.base import ChatMessage, LLMProvider, LLMResponse


class FallbackLLM:
    """Wraps a provider; retries once on the fallback model if the primary call fails."""

    def __init__(self, primary: LLMProvider, fallback_model: str | None) -> None:
        self._primary = primary
        self._fallback_model = fallback_model
        self.name = f"{primary.name}+fallback"

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        try:
            return self._primary.complete(
                messages, model=model, temperature=temperature, max_tokens=max_tokens
            )
        except Exception:
            if self._fallback_model is None:
                raise
            return self._primary.complete(
                messages,
                model=self._fallback_model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/providers/test_fallback.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -v`
Expected: PASS — all Phase 1 + Phase 2 tests green (19 total).

- [ ] **Step 6: Update `README.md`** — in the Status list, change the Phase 2 line to:

```markdown
- [x] Phase 2 — Multi-provider LLM layer (OpenRouter/NVIDIA/OpenAI/Anthropic + fallback)
```

- [ ] **Step 7: Commit**

```bash
git add app/providers/llm/fallback.py tests/providers/test_fallback.py README.md
git commit -m "feat: LLM fallback router and Phase 2 README update"
```

---

## Phase 2 Definition of Done

- `./.venv/Scripts/python.exe -m pytest -v` → all green (Phase 1 + Phase 2).
- Switching `LLM_PROVIDER` between `openrouter`/`nvidia`/`openai`/`anthropic` selects the right adapter (proven by factory tests).
- Fallback retries on the configured model when the primary fails (proven by fallback tests).
- No test makes a network call; no API key is required to run the suite.
- README shows Phase 2 complete.

**Next phase (planned just-in-time):** Phase 3 — the Research Sub-Agent: the `Tool` interface, `web_search` (native/api/mock) + `fetch_url`, and the autonomous tool-calling loop that produces a `ResearchBrief`. (This is where the `complete()` interface grows tool-calling support.)
