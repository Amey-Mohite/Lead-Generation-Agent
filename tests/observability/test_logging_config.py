import json
import logging

from app.observability.logging_config import JSONLogFormatter, request_id_var


def test_format_produces_valid_json_with_expected_keys():
    formatter = JSONLogFormatter()
    record = logging.LogRecord(
        name="app.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello %s", args=("world",), exc_info=None,
    )
    output = formatter.format(record)
    payload = json.loads(output)

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "hello world"
    assert "timestamp" in payload
    assert "request_id" not in payload


def test_format_includes_request_id_when_set():
    formatter = JSONLogFormatter()
    token = request_id_var.set("req-123")
    try:
        record = logging.LogRecord(
            name="app.test", level=logging.WARNING, pathname=__file__, lineno=1,
            msg="careful", args=(), exc_info=None,
        )
        payload = json.loads(formatter.format(record))
        assert payload["request_id"] == "req-123"
    finally:
        request_id_var.reset(token)
