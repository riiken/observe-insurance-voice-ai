"""Google Sheets REST transport.

Knows how to fetch a range of cells and nothing else: no column names, no
domain types, no notion of a customer. Everything above it can therefore be
tested by handing it rows, and this layer can be tested by handing it HTTP
responses.

Reached over the plain `values` REST endpoint rather than
google-api-python-client — one dependency instead of a discovery-document
stack, and an injectable `httpx` transport makes the whole thing mockable
without credentials.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.errors import IntegrationError, IntegrationTimeoutError
from app.core.logging import event, get_logger
from app.core.retry import retry_async
from app.integrations.sheets.auth import SheetsAuthorizer

INTEGRATION_NAME = "google-sheets"

log = get_logger(__name__)

# Status codes worth a second attempt: rate limiting and transient server faults.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, IntegrationTimeoutError):
        return True
    if isinstance(exc, IntegrationError):
        return bool(exc.context.get("retryable"))
    return False


class GoogleSheetsClient:
    """Reads cell ranges from one spreadsheet."""

    def __init__(
        self,
        *,
        spreadsheet_id: str,
        authorizer: SheetsAuthorizer,
        base_url: str = "https://sheets.googleapis.com/v4/spreadsheets",
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        backoff_base_seconds: float = 0.2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._spreadsheet_id = spreadsheet_id
        self._authorizer = authorizer
        self._max_retries = max_retries
        self._backoff_base_seconds = backoff_base_seconds
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
        )

    async def get_values(self, cell_range: str) -> list[list[str]]:
        """Return the rows in `cell_range`, first row included.

        Raises `IntegrationError` (or `IntegrationTimeoutError`) on failure —
        never returns an empty list to mean "something went wrong". Repositories
        translate those into an INTEGRATION_ERROR outcome.
        """
        started = time.perf_counter()

        rows = await retry_async(
            lambda: self._fetch(cell_range),
            max_retries=self._max_retries,
            backoff_base_seconds=self._backoff_base_seconds,
            is_transient=_is_transient,
            operation_name="sheets.get_values",
        )

        log.info(
            "sheets.fetch",
            extra=event(
                range=cell_range,
                rows=len(rows),
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                success=True,
            ),
        )
        return rows

    async def append_values(self, cell_range: str, rows: list[list[str]]) -> int:
        """Append rows to the end of `cell_range`. Returns the rows written.

        Requires a service account: an API key cannot write. Raises
        `IntegrationError` on failure — callers decide whether that is worth
        retrying above the client's own bounded retry.
        """
        if not rows:
            return 0

        started = time.perf_counter()

        written = await retry_async(
            lambda: self._append(cell_range, rows),
            max_retries=self._max_retries,
            backoff_base_seconds=self._backoff_base_seconds,
            is_transient=_is_transient,
            operation_name="sheets.append_values",
        )

        log.info(
            "sheets.append",
            extra=event(
                range=cell_range,
                rows=written,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                success=True,
            ),
        )
        return written

    async def _append(self, cell_range: str, rows: list[list[str]]) -> int:
        headers, params = await self._authorizer.credentials()

        try:
            response = await self._client.post(
                f"/{self._spreadsheet_id}/values/{cell_range}:append",
                headers=headers,
                params={
                    **params,
                    # RAW: a value that looks like a formula must be stored as
                    # text, not evaluated. Interaction records are data.
                    "valueInputOption": "RAW",
                    "insertDataOption": "INSERT_ROWS",
                },
                json={"values": rows},
            )
        except httpx.TimeoutException as exc:
            raise IntegrationTimeoutError(integration=INTEGRATION_NAME, range=cell_range) from exc
        except httpx.HTTPError as exc:
            raise IntegrationError(
                integration=INTEGRATION_NAME,
                range=cell_range,
                cause=type(exc).__name__,
                retryable=True,
            ) from exc

        if response.status_code != httpx.codes.OK:
            raise IntegrationError(
                integration=INTEGRATION_NAME,
                range=cell_range,
                status_code=response.status_code,
                retryable=response.status_code in _RETRYABLE_STATUS,
            )

        return self._parse_append_body(response, cell_range, len(rows))

    @staticmethod
    def _parse_append_body(response: httpx.Response, cell_range: str, requested: int) -> int:
        """A 200 we cannot read is a failure: we must not report a write we
        cannot confirm, or the record is silently lost."""
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise IntegrationError(
                integration=INTEGRATION_NAME, range=cell_range, cause="invalid_json"
            ) from exc

        if not isinstance(payload, dict):
            raise IntegrationError(
                integration=INTEGRATION_NAME, range=cell_range, cause="unexpected_payload"
            )

        updates = payload.get("updates")
        if not isinstance(updates, dict):
            raise IntegrationError(
                integration=INTEGRATION_NAME, range=cell_range, cause="missing_updates"
            )

        updated = updates.get("updatedRows")
        return int(updated) if isinstance(updated, (int, float)) else requested

    async def _fetch(self, cell_range: str) -> list[list[str]]:
        headers, params = await self._authorizer.credentials()

        try:
            response = await self._client.get(
                f"/{self._spreadsheet_id}/values/{cell_range}",
                headers=headers,
                params={**params, "majorDimension": "ROWS"},
            )
        except httpx.TimeoutException as exc:
            raise IntegrationTimeoutError(integration=INTEGRATION_NAME, range=cell_range) from exc
        except httpx.HTTPError as exc:
            # Connection refused, DNS failure, TLS error: worth one more try.
            raise IntegrationError(
                integration=INTEGRATION_NAME,
                range=cell_range,
                cause=type(exc).__name__,
                retryable=True,
            ) from exc

        if response.status_code != httpx.codes.OK:
            raise IntegrationError(
                integration=INTEGRATION_NAME,
                range=cell_range,
                status_code=response.status_code,
                retryable=response.status_code in _RETRYABLE_STATUS,
            )

        return self._parse_body(response, cell_range)

    @staticmethod
    def _parse_body(response: httpx.Response, cell_range: str) -> list[list[str]]:
        """A 200 carrying a body we cannot read is a failure, not an empty sheet."""
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise IntegrationError(
                integration=INTEGRATION_NAME, range=cell_range, cause="invalid_json"
            ) from exc

        if not isinstance(payload, dict):
            raise IntegrationError(
                integration=INTEGRATION_NAME, range=cell_range, cause="unexpected_payload"
            )

        # A genuinely empty range omits "values" entirely — that is a valid
        # empty result, not an error.
        values = payload.get("values", [])
        if not isinstance(values, list):
            raise IntegrationError(
                integration=INTEGRATION_NAME, range=cell_range, cause="unexpected_values"
            )

        return [[str(cell) for cell in row] if isinstance(row, list) else [] for row in values]

    async def aclose(self) -> None:
        await self._client.aclose()
        closer = getattr(self._authorizer, "aclose", None)
        if closer is not None:
            await closer()
