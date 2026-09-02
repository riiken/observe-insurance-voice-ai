"""HTTP middleware: correlation ids and structured access logging."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.context import new_request_id, reset_request_id, set_request_id
from app.core.logging import event, get_logger

REQUEST_ID_HEADER = "X-Request-ID"

log = get_logger(__name__)

_Next = Callable[[Request], Awaitable[Response]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a correlation id to the request and emit one access log line.

    An inbound `X-Request-ID` is honoured so a trace started by the voice
    platform stays joined up; otherwise one is generated. The id is echoed back
    on the response and included in every log line and error body.
    """

    async def dispatch(self, request: Request, call_next: _Next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
        token = set_request_id(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            # The exception handler builds the response; the access line is
            # emitted here so failed requests are never missing from the log.
            self._log_request(request, 500, started, failed=True)
            raise
        else:
            response.headers[REQUEST_ID_HEADER] = request_id
            self._log_request(request, response.status_code, started, failed=False)
            return response
        finally:
            reset_request_id(token)

    @staticmethod
    def _log_request(request: Request, status_code: int, started: float, *, failed: bool) -> None:
        emit = log.warning if failed or status_code >= 500 else log.info
        emit(
            "http.request",
            extra=event(
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            ),
        )


def register_middleware(app: FastAPI) -> None:
    app.add_middleware(RequestContextMiddleware)
