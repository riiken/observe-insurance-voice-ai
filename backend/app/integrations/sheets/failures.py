"""Mapping transport failures onto the repository failure vocabulary.

Shared by both repositories so a timeout is classified identically whichever
sheet was being read.
"""

from __future__ import annotations

from app.core.errors import IntegrationError, IntegrationTimeoutError
from app.integrations.repositories import FailureReason


def failure_reason(exc: IntegrationError) -> FailureReason:
    """Classify an integration failure. Never returns a 'not found' reason."""
    if isinstance(exc, IntegrationTimeoutError):
        return FailureReason.UPSTREAM_TIMEOUT
    # A missing column or an empty sheet is bad data, not an unreachable service;
    # the distinction is what a responder needs to know first.
    if exc.context.get("missing_columns") or exc.context.get("sheet"):
        return FailureReason.MALFORMED_DATA
    return FailureReason.UPSTREAM_ERROR
