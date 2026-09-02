"""Application error hierarchy.

Every error raised by our own code carries a stable machine-readable `code` and
a caller-safe `message`. Anything sensitive stays in `context`, which is logged
but never returned in an HTTP response.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all deliberate application errors."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "Something went wrong on our side."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        **context: Any,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.context: dict[str, Any] = context
        super().__init__(self.message)


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    status_code = 422
    message = "The request could not be processed as submitted."


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = 404
    message = "The requested resource does not exist."


class AuthorizationError(AppError):
    """Raised when the session state does not permit the requested operation.

    This is the hard authentication boundary for claim access in Phase 2 —
    it is decided by session state, never by what the model believes.
    """

    code = "NOT_AUTHORIZED"
    status_code = 403
    message = "This action requires a verified caller."


class IntegrationError(AppError):
    """An external system failed. Explicitly distinct from 'not found'."""

    code = "INTEGRATION_ERROR"
    status_code = 502
    message = "An upstream system is unavailable."

    def __init__(self, message: str | None = None, *, integration: str, **context: Any) -> None:
        super().__init__(message, integration=integration, **context)
        self.integration = integration


class IntegrationTimeoutError(IntegrationError):
    code = "INTEGRATION_TIMEOUT"
    status_code = 504
    message = "An upstream system did not respond in time."


class ServiceUnavailableError(AppError):
    code = "SERVICE_UNAVAILABLE"
    status_code = 503
    message = "The service is not ready to handle requests."
