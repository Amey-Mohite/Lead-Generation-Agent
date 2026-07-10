from collections.abc import Callable


def _default_fetcher(url: str) -> str:
    import httpx

    resp = httpx.get(url, timeout=15.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


class FetchUrlTool:
    name = "fetch_url"
    description = 'Fetch a web page and return its readable text. Args: {"url": string}.'

    def __init__(self, fetcher: Callable[[str], str] | None = None, max_chars: int = 4000) -> None:
        self._fetcher = fetcher or _default_fetcher
        self._max_chars = max_chars

    def run(self, url: str) -> str:
        from bs4 import BeautifulSoup

        html = self._fetcher(url)
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        print(f"Fetched {len(text)} chars from {url}")
        print(f"First 200 chars: {text[:200]}")
        return text[: self._max_chars]
