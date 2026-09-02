"""Request-scoped correlation context.

Values are stored in contextvars so that any log record emitted while handling a
request carries the correlation id without threading it through every call.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_call_id: ContextVar[str | None] = ContextVar("call_id", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex


def set_request_id(request_id: str) -> Token[str | None]:
    return _request_id.set(request_id)


def get_request_id() -> str | None:
    return _request_id.get()


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def set_call_id(call_id: str | None) -> Token[str | None]:
    """Bind the voice-platform call id so every downstream log line carries it."""
    return _call_id.set(call_id)


def get_call_id() -> str | None:
    return _call_id.get()


def reset_call_id(token: Token[str | None]) -> None:
    _call_id.reset(token)
