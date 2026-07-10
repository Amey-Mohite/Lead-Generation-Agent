from app.tools.search.base import SearchBackend


class WebSearchTool:
    name = "web_search"
    description = 'Search the web for a query. Args: {"query": string}. Returns titles, urls, snippets.'

    def __init__(self, backend: SearchBackend, k: int = 5) -> None:
        self._backend = backend
        self._k = k

    def run(self, query: str) -> str:
        print(f"Searching for: {query}")
        results = self._backend.search(query, k=self._k)
        if not results:
            return "No results."
        lines = [
            f"{i + 1}. {r.title}\n   {r.url}\n   {r.snippet}" for i, r in enumerate(results)
        ]
        return "\n".join(lines)
