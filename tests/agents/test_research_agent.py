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
