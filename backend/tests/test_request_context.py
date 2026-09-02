"""Correlation id propagation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.context import get_request_id
from app.core.middleware import REQUEST_ID_HEADER


def test_response_carries_a_generated_request_id(client: TestClient) -> None:
    response = client.get("/health")

    assert response.headers[REQUEST_ID_HEADER]


def test_inbound_request_id_is_honoured(client: TestClient) -> None:
    """A trace started by the voice platform must stay joined up."""
    response = client.get("/health", headers={REQUEST_ID_HEADER: "trace-abc-123"})

    assert response.headers[REQUEST_ID_HEADER] == "trace-abc-123"


def test_each_request_gets_a_distinct_id(client: TestClient) -> None:
    first = client.get("/health").headers[REQUEST_ID_HEADER]
    second = client.get("/health").headers[REQUEST_ID_HEADER]

    assert first != second


def test_request_id_is_available_inside_the_handler(app, client: TestClient) -> None:
    seen: list[str | None] = []

    @app.get("/_test/echo-context")
    async def _echo() -> dict[str, str]:
        seen.append(get_request_id())
        return {"ok": "true"}

    response = client.get("/_test/echo-context", headers={REQUEST_ID_HEADER: "ctx-1"})

    assert response.status_code == 200
    assert seen == ["ctx-1"]


def test_request_id_is_not_leaked_between_requests(client: TestClient) -> None:
    """The contextvar is reset after each request, so nothing leaks."""
    client.get("/health", headers={REQUEST_ID_HEADER: "ctx-leak-check"})

    assert get_request_id() is None
