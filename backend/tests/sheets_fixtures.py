"""Helpers for driving the Sheets adapters without credentials or a network.

Everything external is mocked at the HTTP transport, which is the real seam:
the client's URL building, status handling, retry policy and JSON parsing all
run for real, and only the socket is fake.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import httpx

from app.integrations.sheets.auth import ApiKeyAuthorizer
from app.integrations.sheets.claims import GoogleSheetsClaimsRepository
from app.integrations.sheets.client import GoogleSheetsClient
from app.integrations.sheets.customers import GoogleSheetsCustomerRepository

CUSTOMER_HEADER = ["customer_id", "full_name", "phone_number", "verification_value"]
CLAIM_HEADER = ["claim_id", "customer_id", "status", "required_documents", "last_updated"]

# Mirrors scripts/seed_data/customers.csv.
CUSTOMER_ROWS = [
    CUSTOMER_HEADER,
    ["CUST-1001", "Maria Alvarez", "+1 555 010 1234", "1985-04-12"],
    ["CUST-1002", "James Okonkwo", "+1 555 010 2345", "1979-11-30"],
    ["CUST-1003", "Priya Raman", "+1 555 010 3456", "1992-07-08"],
]

# Mirrors scripts/seed_data/claims.csv.
CLAIM_ROWS = [
    CLAIM_HEADER,
    ["CLM-88401", "CUST-1001", "Under Review", "", "2026-08-28"],
    [
        "CLM-88402",
        "CUST-1002",
        "Documents Required",
        "Police report; Repair estimate",
        "2026-08-30",
    ],
    ["CLM-88406", "CUST-1002", "Approved", "", "2026-03-11"],
]


def values_response(rows: Sequence[Sequence[str]]) -> httpx.Response:
    """A Sheets `values.get` success body."""
    return httpx.Response(
        200, json={"range": "Sheet!A:E", "majorDimension": "ROWS", "values": rows}
    )


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_retries: int = 0,
    backoff_base_seconds: float = 0.0,
) -> GoogleSheetsClient:
    """A client whose socket is a callable. Retries default off for clarity."""
    return GoogleSheetsClient(
        spreadsheet_id="sheet-under-test",
        authorizer=ApiKeyAuthorizer("test-key"),
        max_retries=max_retries,
        backoff_base_seconds=backoff_base_seconds,
        transport=httpx.MockTransport(handler),
    )


def static_handler(rows: Sequence[Sequence[str]]) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _request: values_response(rows)


def customer_repository(
    rows: Sequence[Sequence[str]] | None = None,
    *,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> GoogleSheetsCustomerRepository:
    client = make_client(handler or static_handler(rows if rows is not None else CUSTOMER_ROWS))
    return GoogleSheetsCustomerRepository(client, cell_range="Customers!A:D")


def claims_repository(
    rows: Sequence[Sequence[str]] | None = None,
    *,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> GoogleSheetsClaimsRepository:
    client = make_client(handler or static_handler(rows if rows is not None else CLAIM_ROWS))
    return GoogleSheetsClaimsRepository(client, cell_range="Claims!A:E")
