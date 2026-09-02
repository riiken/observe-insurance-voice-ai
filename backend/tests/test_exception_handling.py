"""Centralised error handling: one envelope, no internal detail leakage."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import AuthorizationError, IntegrationError, NotFoundError
from app.core.middleware import REQUEST_ID_HEADER


@pytest.fixture
def error_client(app: FastAPI, client: TestClient) -> TestClient:
    class _Payload(BaseModel):
        amount: int

    @app.get("/_test/not-found")
    async def _not_found() -> None:
        raise NotFoundError("No claim matches that reference.", claim_id="CLM-1")

    @app.get("/_test/not-authorized")
    async def _not_authorized() -> None:
        raise AuthorizationError(call_id="call-1")

    @app.get("/_test/integration-down")
    async def _integration_down() -> None:
        raise IntegrationError(integration="claims-store", spreadsheet_id="secret-sheet")

    @app.get("/_test/boom")
    async def _boom() -> None:
        raise RuntimeError("database password is hunter2")

    @app.post("/_test/validate")
    async def _validate(payload: _Payload) -> dict[str, int]:
        return {"amount": payload.amount}

    return client


def test_app_error_uses_its_status_and_code(error_client: TestClient) -> None:
    response = error_client.get("/_test/not-found")

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "NOT_FOUND",
        "message": "No claim matches that reference.",
    }


def test_authorization_error_maps_to_403(error_client: TestClient) -> None:
    response = error_client.get("/_test/not-authorized")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_AUTHORIZED"


def test_integration_error_maps_to_502_without_leaking_context(
    error_client: TestClient,
) -> None:
    response = error_client.get("/_test/integration-down")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "INTEGRATION_ERROR"
    assert "secret-sheet" not in response.text


def test_unexpected_error_returns_a_generic_500(error_client: TestClient) -> None:
    response = error_client.get("/_test/boom")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "hunter2" not in response.text


def test_validation_error_does_not_echo_the_submitted_value(
    error_client: TestClient,
) -> None:
    response = error_client.post("/_test/validate", json={"amount": "not-a-number"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "not-a-number" not in response.text


def test_unknown_route_uses_the_same_envelope(error_client: TestClient) -> None:
    response = error_client.get("/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"error", "request_id"}
    assert body["error"]["code"] == "NOT_FOUND"


def test_error_body_carries_the_request_id(error_client: TestClient) -> None:
    response = error_client.get("/_test/not-found", headers={REQUEST_ID_HEADER: "err-1"})

    assert response.json()["request_id"] == "err-1"
