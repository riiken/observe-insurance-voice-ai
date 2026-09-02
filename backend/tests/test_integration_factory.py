"""Wiring Integration #1 from configuration."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.integrations.factory import build_data_integration
from app.integrations.registry import registered_dependencies
from app.integrations.repositories import ClaimsRepository, CustomerRepository
from app.main import create_app


def _configured(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        google_sheets_api_key="test-key",
        google_sheets_spreadsheet_id="sheet-1",
        **overrides,
    )


def test_missing_configuration_yields_no_integration_rather_than_a_crash() -> None:
    settings = Settings(_env_file=None)

    assert settings.sheets_configured is False
    assert build_data_integration(settings) is None


def test_configured_settings_build_both_repositories() -> None:
    integration = build_data_integration(_configured())

    assert integration is not None
    assert isinstance(integration.customers, CustomerRepository)
    assert isinstance(integration.claims, ClaimsRepository)


def test_building_registers_both_repositories_for_readiness() -> None:
    """The readiness hook Phase 1 left open is now filled by Integration #1."""
    build_data_integration(_configured())

    assert {dep.name for dep in registered_dependencies()} == {"customers", "claims"}


def test_partial_configuration_counts_as_unconfigured() -> None:
    settings = Settings(_env_file=None, google_sheets_api_key="key-only")

    assert settings.sheets_configured is False
    assert build_data_integration(settings) is None


def test_the_app_starts_and_stays_live_without_sheets_configuration() -> None:
    """No Google account should be needed to boot and inspect the service."""
    app = create_app(Settings(_env_file=None))

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        ready = client.get("/ready")

    assert ready.status_code == 200
    assert ready.json()["dependencies"] == []


def test_configured_app_exposes_the_integration_on_app_state() -> None:
    app = create_app(_configured())

    with TestClient(app) as client:
        client.get("/health")
        assert app.state.data_integration is not None
