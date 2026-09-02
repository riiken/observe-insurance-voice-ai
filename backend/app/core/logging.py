"""Structured logging.

The stdlib logging module is configured with a JSON formatter so log lines are
machine-parseable in deployment, and a human-readable formatter for local work.
Correlation ids are injected from contextvars, so callers never pass them in.

Usage:
    log = get_logger(__name__)
    log.info("customer.lookup", extra=event(outcome="CUSTOMER_FOUND", duration_ms=42))
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

from app.core.context import get_call_id, get_request_id

# Attributes present on every LogRecord (plus uvicorn's `color_message`);
# anything else was added by the caller via `extra=` and therefore belongs in
# the structured payload.
_RESERVED_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "asctime",
    "message",
    "taskName",
    "color_message",
}

# Field names that must never be written to logs in full.
_REDACTED_KEYS = frozenset({"phone", "caller_phone", "dob", "date_of_birth", "ssn", "email"})


def event(**fields: Any) -> dict[str, Any]:
    """Wrap structured fields for `logger.info(msg, extra=event(...))`."""
    return {"event_fields": fields}


def _redact(key: str, value: Any) -> Any:
    if key.lower() not in _REDACTED_KEYS or value is None:
        return value
    text = str(value)
    return f"***{text[-4:]}" if len(text) > 4 else "***"


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
        }

        if request_id := get_request_id():
            payload["request_id"] = request_id
        if call_id := get_call_id():
            payload["call_id"] = call_id

        for key, value in _collect_extras(record).items():
            payload[key] = _redact(key, value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Compact single-line output for local development."""

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S")
        parts = [f"{stamp} {record.levelname:<7} {record.getMessage()}"]

        if request_id := get_request_id():
            parts.append(f"request_id={request_id[:8]}")
        if call_id := get_call_id():
            parts.append(f"call_id={call_id}")
        parts += [f"{k}={_redact(k, v)}" for k, v in _collect_extras(record).items()]

        line = " ".join(parts)
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def _collect_extras(record: logging.LogRecord) -> dict[str, Any]:
    """Structured fields from `extra=event(...)` plus any bare `extra=` keys."""
    extras: dict[str, Any] = {
        key: value
        for key, value in record.__dict__.items()
        if key not in _RESERVED_ATTRS and key != "event_fields"
    }
    extras.update(getattr(record, "event_fields", {}) or {})
    return extras


def configure_logging(
    level: str = "INFO",
    log_format: str = "json",
    stream: TextIO | None = None,
) -> None:
    """Install the root handler. Safe to call more than once (tests, reload).

    `stream` defaults to stdout, which is where a containerised service belongs;
    tests pass their own buffer.
    """
    formatter = JsonFormatter() if log_format == "json" else ConsoleFormatter()

    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn ships its own handlers; drop them so everything goes through ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # The access log is emitted by our middleware in structured form instead.
    logging.getLogger("uvicorn.access").disabled = True

    # httpx logs every request at INFO *including the full URL* — which for the
    # Sheets API means the API key in a query parameter. Our own `sheets.fetch`
    # line records the same request without the credential.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
