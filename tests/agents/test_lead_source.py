from app.agents.lead_source import NativeSearchSource, WebSearchSource, build_lead_source
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
        self.messages_seen: list = []

    def complete(self, messages, *, model=None, temperature=0.7, max_tokens=None):
        self.messages_seen.append(messages)
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


def test_native_search_source_makes_one_structured_call_no_search_backend():
    scripts = ['{"candidates": [{"name": "Acme Credit Union", "domain": "acme-cu.com"}]}']
    llm = _ScriptedLLM(scripts)
    source = NativeSearchSource(llm)

    candidates = source.discover("credit unions in the UK", max_results=5)

    assert len(candidates) == 1
    assert candidates[0].name == "Acme Credit Union"
    assert llm.calls == 1


def test_native_search_source_tells_the_model_what_to_exclude():
    scripts = ['{"candidates": [{"name": "Beta Credit Union", "domain": "beta-cu.com"}]}']
    llm = _ScriptedLLM(scripts)
    source = NativeSearchSource(llm)

    source.discover("credit unions in the UK", max_results=5, exclude_domains=["acme-cu.com"])

    system_prompt = llm.messages_seen[0][0].content
    assert "acme-cu.com" in system_prompt
    assert "already been found" in system_prompt


def test_web_search_source_tells_the_model_what_to_exclude():
    scripts = ['{"candidates": [{"name": "Beta Credit Union", "domain": "beta-cu.com"}]}']
    llm = _ScriptedLLM(scripts)
    source = WebSearchSource(_FakeSearchBackend(_raw_results()), llm)

    source.discover("credit unions", max_results=5, exclude_domains=["acme-cu.com"])

    system_prompt = llm.messages_seen[0][0].content
    assert "acme-cu.com" in system_prompt


def test_discover_without_excludes_omits_the_exclusion_clause():
    scripts = ['{"candidates": [{"name": "Acme Credit Union", "domain": "acme-cu.com"}]}']
    llm = _ScriptedLLM(scripts)
    source = NativeSearchSource(llm)

    source.discover("credit unions in the UK", max_results=5)

    system_prompt = llm.messages_seen[0][0].content
    assert "already been found" not in system_prompt


def test_build_lead_source_native_mode_returns_native_search_source():
    from app.config import Settings

    s = Settings(
        _env_file=None,
        llm_provider="openrouter",
        llm_model="test-model",
        openrouter_api_key="k",
        lead_search_mode="native",
    )
    source = build_lead_source(s)
    assert isinstance(source, NativeSearchSource)


def test_build_lead_source_api_mode_returns_web_search_source():
    from app.config import Settings

    s = Settings(
        _env_file=None,
        llm_provider="openrouter",
        llm_model="test-model",
        openrouter_api_key="k",
        lead_search_mode="api",
        lead_search_provider="tavily",
        lead_search_api_key="tvly-key",
    )
    source = build_lead_source(s)
    assert isinstance(source, WebSearchSource)


def test_build_lead_source_mock_mode_returns_web_search_source():
    from app.config import Settings

    s = Settings(
        _env_file=None,
        llm_provider="openrouter",
        llm_model="test-model",
        openrouter_api_key="k",
        lead_search_mode="mock",
    )
    source = build_lead_source(s)
    assert isinstance(source, WebSearchSource)
