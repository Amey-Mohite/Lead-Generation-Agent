from app.tools.search.mock import MockSearchBackend
from app.tools.web_search import WebSearchTool


def test_web_search_formats_results():
    tool = WebSearchTool(MockSearchBackend(), k=2)
    out = tool.run(query="acme corp")
    assert tool.name == "web_search"
    assert "acme corp" in out.lower()
    assert "https://example.com" in out
    # both results present
    assert out.lower().count("result") >= 2
