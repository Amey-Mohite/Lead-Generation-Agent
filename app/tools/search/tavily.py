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
