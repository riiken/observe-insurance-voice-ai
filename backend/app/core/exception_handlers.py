"""Centralised exception handling.

Every failure leaves the application through one of these handlers, so responses
share one envelope and one log shape. Internal details are logged, never
returned.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import get_request_id
from app.core.errors import AppError
from app.core.logging import event, get_logger
from app.schemas.errors import ErrorResponse

log = get_logger(__name__)


def _response(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse.model_validate(
        {"error": {"code": code, "message": message}, "request_id": get_request_id()}
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    # 5xx is our fault and worth an ERROR line; 4xx is expected traffic.
    level = log.error if exc.status_code >= 500 else log.warning
    level(
        "app.error",
        extra=event(
            code=exc.code,
            status_code=exc.status_code,
            path=request.url.path,
            **exc.context,
        ),
    )
    return _response(exc.status_code, exc.code, exc.message)


async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = "NOT_FOUND" if exc.status_code == status.HTTP_404_NOT_FOUND else "HTTP_ERROR"
    log.warning(
        "http.error",
        extra=event(status_code=exc.status_code, path=request.url.path),
    )
    return _response(exc.status_code, code, str(exc.detail))


async def handle_request_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # Echo the field locations but not the submitted values, which may be PII.
    fields = [".".join(str(part) for part in err.get("loc", ())) for err in exc.errors()]
    log.warning("request.invalid", extra=event(path=request.url.path, fields=fields))
    return _response(
        422,
        "VALIDATION_ERROR",
        "The request payload was not valid.",
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled.error", extra=event(path=request.url.path))
    return _response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "INTERNAL_ERROR",
        "Something went wrong on our side.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
