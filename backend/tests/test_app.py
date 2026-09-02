"""Application wiring."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_openapi_is_available_outside_production(client: TestClient) -> None:
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_docs_are_disabled_in_production() -> None:
    app = create_app(Settings(_env_file=None, environment="prod"))

    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_health_probes_sit_outside_the_versioned_prefix(client: TestClient) -> None:
    """Probe paths are an operational contract and must not move with the API version."""
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/health").status_code == 404


def test_settings_are_attached_to_app_state(app) -> None:
    assert isinstance(app.state.settings, Settings)


def test_routes_use_the_settings_the_app_was_built_with() -> None:
    """Injected settings must win over the process-wide cached settings."""
    app = create_app(Settings(_env_file=None, environment="staging", app_name="explicit-name"))

    with TestClient(app) as client:
        body = client.get("/health").json()

    assert body["environment"] == "staging"
    assert body["service"] == "explicit-name"


def test_settings_dependency_falls_back_when_state_is_unset() -> None:
    """A bare FastAPI app (no create_app) still resolves settings."""
    from fastapi import FastAPI

    from app.api.health import router

    bare = FastAPI()
    bare.include_router(router)

    with TestClient(bare) as client:
        assert client.get("/health").status_code == 200
