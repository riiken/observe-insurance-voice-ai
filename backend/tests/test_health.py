"""Liveness and readiness endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import __version__
from app.integrations.base import DependencyStatus
from app.integrations.registry import register_dependency


class _StubDependency:
    def __init__(self, name: str, *, healthy: bool = True, raises: bool = False) -> None:
        self.name = name
        self._healthy = healthy
        self._raises = raises

    async def check_health(self) -> DependencyStatus:
        if self._raises:
            raise RuntimeError("upstream exploded")
        return DependencyStatus(self.name, healthy=self._healthy)


def test_health_reports_service_metadata(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["environment"] == "local"


def test_health_does_not_probe_dependencies(client: TestClient) -> None:
    """Liveness must stay up even when every upstream is down."""
    register_dependency(_StubDependency("claims-store", healthy=False))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_is_ready_with_no_dependencies(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["dependencies"] == []


def test_ready_reports_healthy_dependencies(client: TestClient) -> None:
    register_dependency(_StubDependency("claims-store"))
    register_dependency(_StubDependency("interaction-log"))

    response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert {d["name"] for d in body["dependencies"]} == {"claims-store", "interaction-log"}
    assert all(d["duration_ms"] is not None for d in body["dependencies"])


def test_ready_returns_503_when_a_dependency_is_unhealthy(client: TestClient) -> None:
    register_dependency(_StubDependency("claims-store", healthy=False))
    register_dependency(_StubDependency("interaction-log", healthy=True))

    response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert {d["name"]: d["healthy"] for d in body["dependencies"]} == {
        "claims-store": False,
        "interaction-log": True,
    }


def test_ready_survives_a_probe_that_raises(client: TestClient) -> None:
    """A broken probe degrades readiness; it never propagates as a 500."""
    register_dependency(_StubDependency("claims-store", raises=True))

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["dependencies"][0]["detail"] == "RuntimeError"
