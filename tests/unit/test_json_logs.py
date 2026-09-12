"""JSON logs + request_id (TZ P2.3)."""

from __future__ import annotations

import json
import logging

from astra_bot.core.logger import JsonFormatter
from astra_bot.core.request_context import set_request_id


def test_json_formatter_includes_request_id():
    set_request_id("abc123")
    record = logging.LogRecord(
        name="astra.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    payload = json.loads(JsonFormatter().format(record))
    assert payload["msg"] == "hello world"
    assert payload["request_id"] == "abc123"
    assert payload["level"] == "INFO"
    assert "ts" in payload
    set_request_id("-")
