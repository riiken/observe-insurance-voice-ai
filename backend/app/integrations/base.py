"""Contracts every external integration implements.

Business logic depends on these protocols, never on a vendor SDK. Swapping
Google Sheets for a real claims API in a later phase should touch only the
concrete adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    """Result of one dependency's readiness probe."""

    name: str
    healthy: bool
    detail: str | None = None
    duration_ms: float | None = None


@runtime_checkable
class HealthCheckable(Protocol):
    """An external dependency that readiness can probe.

    Implementations must not raise: a failed probe is reported as an unhealthy
    `DependencyStatus` so one broken dependency cannot take the process down.
    """

    name: str

    async def check_health(self) -> DependencyStatus: ...
