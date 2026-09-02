"""Liveness and readiness endpoints.

These sit outside the versioned API prefix: they are operational contracts for
the platform (load balancer, container orchestrator), not part of the product
API, so they must not move when the API version bumps.

Liveness answers "is this process alive" and stays cheap and dependency-free, so
an orchestrator never restarts a healthy process because an upstream is slow.
Readiness answers "should traffic be routed here" and does consult dependencies.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Response, status

from app import __version__
from app.api.dependencies import SettingsDep
from app.core.metrics import METRICS
from app.integrations.registry import cached_check_all
from app.schemas.health import DependencyHealth, HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health(settings: SettingsDep) -> HealthResponse:
    return HealthResponse(
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
    )


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def ready(settings: SettingsDep, response: Response) -> ReadinessResponse:
    statuses = await cached_check_all(settings.readiness_cache_seconds)
    is_ready = all(dependency.healthy for dependency in statuses)

    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
        dependencies=[DependencyHealth(**asdict(s)) for s in statuses],
    )


@router.get("/metrics", summary="Operational metrics")
async def metrics() -> dict[str, object]:
    """Counters, latencies and the derived rates, for this process.

    Contains no call ids, no customer data and no secrets — only counts and
    durations — so it is safe to scrape. It is still operational information
    (call volumes, failure rates), so a deployment should keep it on an
    internal network rather than the public internet.

    Process-local and reset by a restart. That is the honest limit of not
    running a metrics backend; see the scaling notes in the README.
    """
    return METRICS.snapshot()
