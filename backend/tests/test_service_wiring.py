"""Service-layer wiring and availability."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import ClaimsServiceDep, get_services
from app.core.config import Settings
from app.core.errors import ServiceUnavailableError
from app.main import create_app
from app.services.authentication import AuthenticationService
from app.services.claims import ClaimsService
from app.services.container import ServiceContainer, build_services
from app.services.session_store import SessionStore
from tests.session_fixtures import FakeClaimsRepository, FakeCustomerRepository


class _Integration:
    def __init__(self) -> None:
        self.customers = FakeCustomerRepository()
        self.claims = FakeClaimsRepository()


def _configured() -> Settings:
    return Settings(
        _env_file=None,
        google_sheets_api_key="test-key",
        google_sheets_spreadsheet_id="sheet-1",
    )


def test_build_services_produces_the_whole_layer() -> None:
    services = build_services(_Integration())  # type: ignore[arg-type]

    assert isinstance(services, ServiceContainer)
    assert isinstance(services.authentication, AuthenticationService)
    assert isinstance(services.claims, ClaimsService)
    assert isinstance(services.sessions, SessionStore)


def test_both_services_share_one_session_store() -> None:
    """Authenticating in one service must be visible to the other."""
    services = build_services(_Integration())  # type: ignore[arg-type]

    assert services.authentication._sessions is services.sessions
    assert services.claims._sessions is services.sessions


def test_a_configured_app_builds_the_service_layer() -> None:
    app = create_app(_configured())

    with TestClient(app):
        assert isinstance(app.state.services, ServiceContainer)


def test_an_unconfigured_app_has_no_service_layer_but_still_runs() -> None:
    app = create_app(Settings(_env_file=None))

    with TestClient(app) as client:
        assert app.state.services is None
        assert client.get("/health").status_code == 200


def test_the_dependency_reports_503_when_the_integration_is_absent() -> None:
    app = create_app(Settings(_env_file=None))

    @app.get("/_test/claims")
    async def _claims(claims: ClaimsServiceDep) -> dict[str, str]:
        return {"ok": "true"}

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/_test/claims")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


def test_the_dependency_supplies_the_service_when_configured() -> None:
    app = create_app(_configured())
    seen: list[ClaimsService] = []

    @app.get("/_test/claims")
    async def _claims(claims: ClaimsServiceDep) -> dict[str, str]:
        seen.append(claims)
        return {"ok": "true"}

    with TestClient(app) as client:
        assert client.get("/_test/claims").status_code == 200

    assert isinstance(seen[0], ClaimsService)


def test_get_services_raises_rather_than_returning_none() -> None:
    """A None service must never be handed out to be checked (or forgotten)."""

    class _Request:
        app = FastAPI()

    with pytest.raises(ServiceUnavailableError):
        get_services(_Request())  # type: ignore[arg-type]


def test_services_are_torn_down_on_shutdown() -> None:
    app = create_app(_configured())

    with TestClient(app):
        pass

    assert app.state.services is None
