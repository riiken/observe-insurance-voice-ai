"""Structured logging: shape, correlation and redaction."""

from __future__ import annotations

import io
import json
import logging
import sys
from collections.abc import Callable

import pytest

from app.core.context import reset_call_id, reset_request_id, set_call_id, set_request_id
from app.core.logging import JsonFormatter, configure_logging, event, get_logger


@pytest.fixture
def json_logs() -> Callable[[], list[dict]]:
    """Configure JSON logging into a buffer and read back the emitted records."""
    buffer = io.StringIO()
    configure_logging(level="DEBUG", log_format="json", stream=buffer)

    def _read() -> list[dict]:
        return [json.loads(line) for line in buffer.getvalue().splitlines() if line]

    return _read


def test_log_line_is_json_with_the_expected_shape(json_logs) -> None:
    get_logger("test").info("customer.lookup", extra=event(outcome="CUSTOMER_FOUND"))

    record = json_logs()[0]
    assert record["event"] == "customer.lookup"
    assert record["level"] == "INFO"
    assert record["outcome"] == "CUSTOMER_FOUND"
    assert record["timestamp"].endswith("+00:00")


def test_correlation_ids_are_injected_from_context(json_logs) -> None:
    request_token = set_request_id("req-1")
    call_token = set_call_id("call-9")
    try:
        get_logger("test").info("call.started")
    finally:
        reset_call_id(call_token)
        reset_request_id(request_token)

    record = json_logs()[0]
    assert record["request_id"] == "req-1"
    assert record["call_id"] == "call-9"


def test_correlation_ids_are_omitted_outside_a_request(json_logs) -> None:
    get_logger("test").info("app.started")

    assert "request_id" not in json_logs()[0]
    assert "call_id" not in json_logs()[0]


def test_sensitive_fields_are_redacted(json_logs) -> None:
    get_logger("test").info("customer.lookup", extra=event(caller_phone="+15551234567"))

    assert json_logs()[0]["caller_phone"] == "***4567"


def test_exceptions_are_captured(json_logs) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        get_logger("test").exception("tool.error")

    record = json_logs()[0]
    assert record["level"] == "ERROR"
    assert "ValueError: boom" in record["exception"]


def test_console_format_is_single_line_and_readable() -> None:
    buffer = io.StringIO()
    configure_logging(level="DEBUG", log_format="console", stream=buffer)

    get_logger("test").info("faq.lookup", extra=event(topic="office_hours"))

    line = buffer.getvalue().strip()
    assert line.count("\n") == 0
    assert "faq.lookup" in line
    assert "topic=office_hours" in line


def test_configure_logging_defaults_to_stdout() -> None:
    configure_logging(level="INFO", log_format="json")

    handler = logging.getLogger().handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stdout


def test_configure_logging_is_idempotent() -> None:
    configure_logging(level="INFO", log_format="json", stream=io.StringIO())
    configure_logging(level="INFO", log_format="json", stream=io.StringIO())

    assert len(logging.getLogger().handlers) == 1


def test_http_client_logging_cannot_leak_credentials() -> None:
    """httpx logs full URLs at INFO, and the Sheets key travels in the query."""
    configure_logging(level="DEBUG", log_format="json", stream=io.StringIO())

    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


def test_json_formatter_never_raises_on_unserialisable_values() -> None:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "tool.error", None, None)
    record.event_fields = {"value": object()}

    assert json.loads(JsonFormatter().format(record))["event"] == "tool.error"
