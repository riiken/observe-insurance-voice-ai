"""Registry of dependencies that readiness probes.

Phase 1 registers nothing, so the service is ready as soon as it is up. Phase 3
registers the customer/claims and interaction-log adapters here, and readiness
starts reflecting them without the endpoint changing.
"""

from __future__ import annotations

import asyncio
import time

from app.core.logging import event, get_logger
from app.integrations.base import DependencyStatus, HealthCheckable

log = get_logger(__name__)

_PROBE_TIMEOUT_SECONDS = 3.0

_dependencies: list[HealthCheckable] = []


def register_dependency(dependency: HealthCheckable) -> None:
    _dependencies.append(dependency)


def registered_dependencies() -> tuple[HealthCheckable, ...]:
    return tuple(_dependencies)


def clear_dependencies() -> None:
    """Test hook; also used when the app is torn down."""
    _dependencies.clear()


async def _probe(dependency: HealthCheckable) -> DependencyStatus:
    started = time.perf_counter()
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            status = await dependency.check_health()
    except TimeoutError:
        status = DependencyStatus(dependency.name, healthy=False, detail="probe timed out")
    except Exception as exc:  # a broken probe must not break readiness
        log.warning("dependency.probe_failed", extra=event(dependency=dependency.name))
        status = DependencyStatus(dependency.name, healthy=False, detail=type(exc).__name__)

    if status.duration_ms is None:
        status = DependencyStatus(
            name=status.name,
            healthy=status.healthy,
            detail=status.detail,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    return status


async def check_all() -> list[DependencyStatus]:
    """Probe every registered dependency concurrently."""
    if not _dependencies:
        return []
    return list(await asyncio.gather(*(_probe(dep) for dep in _dependencies)))
