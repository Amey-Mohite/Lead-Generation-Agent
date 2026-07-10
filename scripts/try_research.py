"""Manual end-to-end check of the Research Sub-Agent.

Usage:
    ./.venv/Scripts/python.exe scripts/try_research.py [target] [--demo]

Behaviour:
- If an API key for the configured LLM_PROVIDER is present, does a REAL run using
  RESEARCH_SEARCH_MODE from .env (mock/api/native) and prints a live trace of every
  turn (prompt tail -> model reply). Built via build_research_agent(), so it exactly
  matches production wiring -- including native mode (OpenRouter ":online" models,
  no separate search key) and api mode (Tavily, needs SEARCH_API_KEY).
- If no key is found (or --demo is passed), runs an OFFLINE scripted demo (no network,
  no keys) so you can still watch the ReAct loop drive a tool and finalize a brief.
"""

import sys

from app.agents.research_agent import ResearchAgent
from app.config import get_settings
from app.providers.llm.base import LLMResponse
from app.tools.base import ToolRegistry
from app.tools.fetch_url import FetchUrlTool
from app.tools.search.mock import MockSearchBackend
from app.tools.web_search import WebSearchTool

_KEY_ATTR = {
    "openrouter": "openrouter_api_key",
    "nvidia": "nvidia_api_key",
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
}


class TracingLLM:
    """Wraps any LLMProvider and prints each turn — shows the loop working."""

    def __init__(self, inner):
        self._inner = inner
        self.name = f"tracing({inner.name})"
        self._turn = 0

    def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
        self._turn += 1
        print(f"\n=== LLM call #{self._turn} — latest message to model ===")
        print(messages[-1].content[:600])
        resp = self._inner.complete(
            messages, model=model, temperature=temperature, max_tokens=max_tokens
        )
        print(f"\n--- model replied ---\n{resp.content[:600]}\n")
        return resp


class ScriptedLLM:
    """Offline stand-in: emits a search action, then a final brief. No keys/network."""

    name = "scripted-demo"

    def __init__(self, target):
        self._scripts = [
            '{"action": {"tool": "web_search", "args": {"query": "%s company overview"}}}'
            % target,
            '{"final": {"company_name": "%s", "domain": "%s", '
            '"industry": "(demo)", "summary": "Offline demo brief for %s.", '
            '"key_facts": ["This is a scripted demo, not real research."], '
            '"sources": ["https://example.com"]}}' % (target, target, target),
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
        from app.agents.research_agent import build_research_agent

        print(f"[REAL run] provider={settings.llm_provider} model={settings.llm_model} "
              f"search_mode={settings.research_search_mode} target={target}")
        agent = build_research_agent(settings)
        print(f"Agent built: {agent._llm.name} + {len(agent._registry._tools)} tools")
        agent._llm = TracingLLM(agent._llm)  # visibility only, doesn't change behaviour
    else:
        why = "forced --demo" if force_demo else f"no API key for '{settings.llm_provider}'"
        print(f"[OFFLINE demo] ({why}) target={target}\n"
              f"  -> set a key in .env (e.g. OPENROUTER_API_KEY) for a real run.")
        registry = ToolRegistry([WebSearchTool(MockSearchBackend()), FetchUrlTool()])
        agent = ResearchAgent(ScriptedLLM(target), registry, max_steps=6)

    brief = agent.run(target)

    print("\n================ RESEARCH BRIEF ================")
    print(brief.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
