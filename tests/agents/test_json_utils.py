from app.agents.json_utils import extract_json_object


def test_plain_json():
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_fenced_json_with_prose():
    text = 'Sure!\n```json\n{"tool": "web_search", "args": {"query": "x"}}\n```\ndone'
    assert extract_json_object(text) == {"tool": "web_search", "args": {"query": "x"}}


def test_unparseable_returns_none():
    assert extract_json_object("no json here") is None
