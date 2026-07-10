from app.tools.fetch_url import FetchUrlTool

_HTML = "<html><body><h1>Acme</h1><p>We make widgets.</p><script>x=1</script></body></html>"


def test_fetch_url_extracts_text_without_scripts():
    tool = FetchUrlTool(fetcher=lambda url: _HTML)
    out = tool.run(url="https://acme.com")
    assert tool.name == "fetch_url"
    assert "Acme" in out
    assert "We make widgets." in out
    assert "x=1" not in out  # script content stripped


def test_fetch_url_truncates():
    big = "<p>" + ("a" * 10000) + "</p>"
    tool = FetchUrlTool(fetcher=lambda url: big, max_chars=100)
    out = tool.run(url="https://x.com")
    assert len(out) <= 100
