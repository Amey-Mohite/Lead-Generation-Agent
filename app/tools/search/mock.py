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
