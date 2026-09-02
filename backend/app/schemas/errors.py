"""Wire format for error responses."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Caller-safe description. Never contains internals.")


class ErrorResponse(BaseModel):
    """The single error envelope returned by every failing endpoint."""

    error: ErrorDetail
    request_id: str | None = Field(
        default=None, description="Correlation id; quote this when reporting a problem."
    )
