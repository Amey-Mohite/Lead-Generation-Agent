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

_NATIVE_DISCOVER_SYSTEM = """You are a lead discovery agent with live web search access. Search
the web and find real, specific companies matching the given query/category.

Respond with ONE JSON object and nothing else:
{{"candidates": [{{"name": "...", "domain": "..."}}, ...]}}

Rules:
- Only include real, specific companies you found via web search -- do not invent any.
- "domain" is the company's own website domain (e.g. "acme.com"), not a directory or listing page.
- Return at most {max_results} candidates; fewer is fine if that is all you can verify."""


def _with_exclusions(system_prompt: str, exclude_domains: list[str] | None) -> str:
    if not exclude_domains:
        return system_prompt
    excluded = "\n".join(f"- {domain}" for domain in exclude_domains)
    return (
        f"{system_prompt}\n\n"
        "Do NOT include any of these companies -- they have already been found and processed; "
        f"find different ones instead:\n{excluded}"
    )


class LeadSource(Protocol):
    def discover(
        self, query: str, max_results: int, exclude_domains: list[str] | None = None
    ) -> list[Candidate]: ...


class WebSearchSource:
    """Discovers candidates via an explicit SearchBackend (api/mock modes) + one structured
    extraction call over the raw results."""

    def __init__(self, search_backend: SearchBackend, llm: LLMProvider) -> None:
        self._search_backend = search_backend
        self._llm = llm

    def discover(
        self, query: str, max_results: int, exclude_domains: list[str] | None = None
    ) -> list[Candidate]:
        raw_results = self._search_backend.search(query, k=max_results * 3)
        formatted = "\n".join(
            f"{i + 1}. {r.title}\n   {r.url}\n   {r.snippet}"
            for i, r in enumerate(raw_results)
        )
        system = _with_exclusions(_EXTRACT_SYSTEM.format(max_results=max_results), exclude_domains)
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(
                role="user", content=f"Query: {query}\n\nSearch results:\n{formatted}"
            ),
        ]
        result = complete_structured(self._llm, messages, CandidateList)
        assert isinstance(result, CandidateList)
        return result.candidates[:max_results]


class NativeSearchSource:
    """Discovers candidates via the LLM's own built-in web search (an OnlineSearchLLM-wrapped
    provider is expected) -- no separate SearchBackend involved at all."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def discover(
        self, query: str, max_results: int, exclude_domains: list[str] | None = None
    ) -> list[Candidate]:
        system = _with_exclusions(
            _NATIVE_DISCOVER_SYSTEM.format(max_results=max_results), exclude_domains
        )
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=f"Query: {query}"),
        ]
        result = complete_structured(self._llm, messages, CandidateList)
        assert isinstance(result, CandidateList)
        return result.candidates[:max_results]


def build_lead_source(settings) -> LeadSource:
    from app.providers.llm.factory import build_llm_provider
    from app.providers.llm.fallback import FallbackLLM
    from app.providers.llm.online import OnlineSearchLLM
    from app.tools.search.mock import MockSearchBackend
    from app.tools.search.tavily import TavilySearchBackend

    base_llm = build_llm_provider(settings)

    if settings.lead_search_mode == "native":
        llm = FallbackLLM(OnlineSearchLLM(base_llm), settings.llm_fallback_model)
        return NativeSearchSource(llm)

    if settings.lead_search_mode == "api" and settings.lead_search_provider == "tavily":
        search_backend = TavilySearchBackend(api_key=settings.lead_search_api_key)
    else:
        search_backend = MockSearchBackend()

    llm = FallbackLLM(base_llm, settings.llm_fallback_model)
    return WebSearchSource(search_backend, llm)
