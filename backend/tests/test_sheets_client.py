"""The Sheets transport: URLs, failure classification and retry policy."""

from __future__ import annotations

import httpx
import pytest

from app.core.errors import IntegrationError, IntegrationTimeoutError
from tests.sheets_fixtures import make_client, static_handler, values_response


async def test_reads_the_rows_of_a_range() -> None:
    client = make_client(static_handler([["a", "b"], ["1", "2"]]))

    assert await client.get_values("Customers!A:D") == [["a", "b"], ["1", "2"]]

    await client.aclose()


async def test_request_targets_the_values_endpoint_with_the_api_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return values_response([["a"]])

    client = make_client(handler)
    await client.get_values("Customers!A:D")

    assert seen[0].url.path.endswith("/sheet-under-test/values/Customers!A:D")
    assert seen[0].url.params["key"] == "test-key"
    await client.aclose()


async def test_an_empty_range_is_an_empty_list_not_an_error() -> None:
    """Sheets omits `values` entirely for an empty range."""
    client = make_client(lambda _r: httpx.Response(200, json={"range": "A:D"}))

    assert await client.get_values("Customers!A:D") == []

    await client.aclose()


async def test_timeout_raises_a_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    client = make_client(handler)

    with pytest.raises(IntegrationTimeoutError):
        await client.get_values("Customers!A:D")

    await client.aclose()


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
async def test_client_errors_raise_and_are_not_retried(status_code: int) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code, json={"error": {"message": "nope"}})

    client = make_client(handler, max_retries=3)

    with pytest.raises(IntegrationError):
        await client.get_values("Customers!A:D")

    assert attempts == 1
    await client.aclose()


@pytest.mark.parametrize("status_code", [429, 500, 503])
async def test_transient_errors_are_retried_within_budget(status_code: int) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code)

    client = make_client(handler, max_retries=2, backoff_base_seconds=0.0)

    with pytest.raises(IntegrationError):
        await client.get_values("Customers!A:D")

    assert attempts == 3
    await client.aclose()


async def test_a_transient_error_that_clears_returns_data() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503)
        return values_response([["a"]])

    client = make_client(handler, max_retries=2, backoff_base_seconds=0.0)

    assert await client.get_values("Customers!A:D") == [["a"]]
    assert attempts == 2
    await client.aclose()


async def test_connection_failure_is_an_integration_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = make_client(handler)

    with pytest.raises(IntegrationError) as caught:
        await client.get_values("Customers!A:D")

    assert not isinstance(caught.value, IntegrationTimeoutError)
    await client.aclose()


@pytest.mark.parametrize("body", [b"not json at all", b'"a string"', b'{"values": "not a list"}'])
async def test_an_unreadable_200_body_is_a_failure_not_an_empty_sheet(body: bytes) -> None:
    """Returning [] here would read as 'no customers' — the exact conflation to avoid."""
    client = make_client(lambda _r: httpx.Response(200, content=body))

    with pytest.raises(IntegrationError):
        await client.get_values("Customers!A:D")

    await client.aclose()


async def test_error_context_does_not_leak_the_api_key() -> None:
    client = make_client(lambda _r: httpx.Response(500))

    with pytest.raises(IntegrationError) as caught:
        await client.get_values("Customers!A:D")

    assert "test-key" not in repr(caught.value.context)
    assert "test-key" not in str(caught.value)
    await client.aclose()
