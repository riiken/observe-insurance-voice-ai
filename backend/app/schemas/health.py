"""Response models for the liveness and readiness endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness: the process is up. Never touches external systems."""

    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str


class DependencyHealth(BaseModel):
    name: str
    healthy: bool
    detail: str | None = None
    duration_ms: float | None = None


class ReadinessResponse(BaseModel):
    """Readiness: the process can serve traffic, dependencies included."""

    status: Literal["ready", "not_ready"]
    service: str
    version: str
    environment: str
    dependencies: list[DependencyHealth] = Field(default_factory=list)
