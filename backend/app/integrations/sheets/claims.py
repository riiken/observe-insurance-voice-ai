"""Google Sheets implementation of `ClaimsRepository`."""

from __future__ import annotations

import time
from datetime import date

from app.core.errors import IntegrationError
from app.core.logging import event, get_logger
from app.integrations.base import DependencyStatus
from app.integrations.repositories import ClaimLookupResult
from app.integrations.sheets.client import GoogleSheetsClient
from app.integrations.sheets.failures import failure_reason
from app.integrations.sheets.rows import CLAIM_COLUMNS, index_header, parse_claim_row
from app.models.claim import Claim

log = get_logger(__name__)


class GoogleSheetsClaimsRepository:
    """Reads the Claims sheet."""

    name = "claims"

    def __init__(self, client: GoogleSheetsClient, *, cell_range: str = "Claims!A:E") -> None:
        self._client = client
        self._cell_range = cell_range

    async def get_claim_for_customer(self, customer_id: str) -> ClaimLookupResult:
        if not customer_id:
            return ClaimLookupResult.not_found()

        started = time.perf_counter()
        try:
            claims = await self._load_rows()
        except IntegrationError as exc:
            log.error(
                "claim.lookup",
                extra=event(
                    outcome="INTEGRATION_ERROR",
                    error_code=exc.code,
                    customer_id=customer_id,
                    success=False,
                ),
            )
            return ClaimLookupResult.integration_error(failure_reason(exc))

        matches = [claim for claim in claims if claim.customer_id == customer_id]
        duration_ms = round((time.perf_counter() - started) * 1000, 2)

        if not matches:
            log.info(
                "claim.lookup",
                extra=event(
                    outcome="CLAIM_NOT_FOUND", customer_id=customer_id, duration_ms=duration_ms
                ),
            )
            return ClaimLookupResult.not_found()

        claim = _most_recent(matches)
        log.info(
            "claim.lookup",
            extra=event(
                outcome="CLAIM_FOUND",
                customer_id=customer_id,
                claim_id=claim.claim_id,
                status=claim.status,
                duration_ms=duration_ms,
            ),
        )
        return ClaimLookupResult.found(claim)

    async def check_health(self) -> DependencyStatus:
        started = time.perf_counter()
        try:
            await self._load_rows()
        except IntegrationError as exc:
            return DependencyStatus(self.name, healthy=False, detail=exc.code)

        return DependencyStatus(
            self.name,
            healthy=True,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    async def _load_rows(self) -> list[Claim]:
        values = await self._client.get_values(self._cell_range)
        if not values:
            raise IntegrationError(
                "The Claims sheet is empty.", integration="google-sheets", sheet="Claims"
            )

        header, *data = values
        index = index_header(header, CLAIM_COLUMNS, sheet="Claims")

        return [
            claim
            for offset, raw in enumerate(data)
            if (claim := parse_claim_row(raw, index, row_number=offset + 2)) is not None
        ]


def _most_recent(claims: list[Claim]) -> Claim:
    """A customer may hold several claims; the current one is the last updated.

    Claims with no readable date sort last rather than being dropped, so a
    missing date never hides a claim entirely.
    """
    return max(claims, key=lambda claim: claim.last_updated or date.min)
