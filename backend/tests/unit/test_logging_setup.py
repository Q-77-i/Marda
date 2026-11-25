"""J8-A7：结构化日志的纯函数面。"""

import json
import logging

from app import logging_setup


def test_parse_trace_id_accepts_w3c():
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    assert logging_setup.parse_trace_id(f"00-{trace_id}-00f067aa0ba902b7-01") == trace_id


def test_parse_trace_id_rejects_malformed():
    assert logging_setup.parse_trace_id(None) == ""
    assert logging_setup.parse_trace_id("not-a-traceparent") == ""
    assert logging_setup.parse_trace_id("00-xyz-00f067aa0ba902b7-01") == ""


def test_json_formatter_includes_context(monkeypatch):
    token_r = logging_setup.request_id_var.set("req-1")
    token_t = logging_setup.trace_id_var.set("a" * 32)
    token_i = logging_setup.instance_var.set("api-1")
    try:
        record = logging.LogRecord(
            name="app.test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="hello %s", args=("world",), exc_info=None,
        )
        payload = json.loads(logging_setup.JsonFormatter().format(record))
        assert payload["message"] == "hello world"
        assert payload["level"] == "INFO"
        assert payload["request_id"] == "req-1"
        assert payload["trace_id"] == "a" * 32
        assert payload["instance"] == "api-1"
    finally:
        logging_setup.instance_var.reset(token_i)
        logging_setup.trace_id_var.reset(token_t)
        logging_setup.request_id_var.reset(token_r)
