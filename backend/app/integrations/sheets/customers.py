"""Google Sheets implementation of `CustomerRepository`."""

from __future__ import annotations

import hmac
import time

from app.core import events
from app.core.errors import IntegrationError
from app.core.logging import event, get_logger
from app.core.phone import normalize_phone
from app.integrations.base import DependencyStatus
from app.integrations.repositories import (
    CustomerLookupResult,
    FailureReason,
    VerificationResult,
)
from app.integrations.sheets.client import GoogleSheetsClient
from app.integrations.sheets.failures import failure_reason
from app.integrations.sheets.rows import (
    CUSTOMER_COLUMNS,
    CustomerRow,
    index_header,
    parse_customer_row,
)

log = get_logger(__name__)


class GoogleSheetsCustomerRepository:
    """Reads the Customers sheet.

    The sheet is scanned in full on each lookup. That is honest for a demo-sized
    customer list and keeps the repository stateless; a cache is deferred rather
    than assumed (see docs/DEFERRED.md).
    """

    name = "customers"

    def __init__(
        self,
        client: GoogleSheetsClient,
        *,
        cell_range: str = "Customers!A:D",
        default_country_code: str = "+1",
    ) -> None:
        self._client = client
        self._cell_range = cell_range
        self._default_country_code = default_country_code

    async def lookup_customer_by_phone(self, phone_number: str) -> CustomerLookupResult:
        normalised = normalize_phone(phone_number, default_country_code=self._default_country_code)

        if normalised is None:
            # Nothing was wrong upstream and nothing is missing from the sheet;
            # we simply were not given a phone number.
            log.info(
                events.CUSTOMER_LOOKUP_COMPLETED,
                extra=event(
                    outcome="CUSTOMER_NOT_FOUND",
                    reason="INVALID_PHONE_NUMBER",
                    success=False,
                    duration_ms=0.0,
                ),
            )
            return CustomerLookupResult.not_found(FailureReason.INVALID_PHONE_NUMBER)

        log.info(events.CUSTOMER_LOOKUP_STARTED, extra=event(caller_phone=normalised))
        started = time.perf_counter()
        try:
            rows = await self._load_rows()
        except IntegrationError as exc:
            return self._lookup_failure(exc, phone=normalised)

        match = next((row for row in rows if row.phone_number == normalised), None)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)

        if match is None:
            log.info(
                events.CUSTOMER_LOOKUP_COMPLETED,
                extra=event(
                    outcome="CUSTOMER_NOT_FOUND",
                    reason="NO_MATCHING_RECORD",
                    caller_phone=normalised,
                    duration_ms=duration_ms,
                    success=True,  # the lookup worked; the answer is "no record"
                ),
            )
            return CustomerLookupResult.not_found()

        log.info(
            events.CUSTOMER_LOOKUP_COMPLETED,
            extra=event(
                outcome="CUSTOMER_FOUND",
                customer_id=match.customer_id,
                caller_phone=normalised,
                duration_ms=duration_ms,
                success=True,
            ),
        )
        return CustomerLookupResult.found(match.to_customer())

    async def verify_customer(
        self, customer_id: str, verification_value: str
    ) -> VerificationResult:
        if not customer_id or not verification_value.strip():
            # An empty answer is a failed attempt, not a system fault.
            log.info(
                events.AUTHENTICATION_FAILED,
                extra=event(customer_id=customer_id or None, reason="EMPTY_VALUE"),
            )
            return VerificationResult.failed()

        try:
            rows = await self._load_rows()
        except IntegrationError as exc:
            return VerificationResult.integration_error(failure_reason(exc))

        match = next((row for row in rows if row.customer_id == customer_id), None)
        if match is None:
            log.warning(events.AUTHENTICATION_FAILED, extra=event(reason="CUSTOMER_NOT_FOUND"))
            return VerificationResult.customer_not_found()

        if not _values_match(match.verification_value, verification_value):
            log.info(
                events.AUTHENTICATION_FAILED,
                extra=event(customer_id=customer_id, reason="VALUE_MISMATCH"),
            )
            return VerificationResult.failed()

        log.info(events.AUTHENTICATION_SUCCESS, extra=event(customer_id=customer_id))
        return VerificationResult.verified(match.to_customer())

    async def check_health(self) -> DependencyStatus:
        """Readiness probe: can we read the sheet and does it have our columns?"""
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

    async def _load_rows(self) -> list[CustomerRow]:
        values = await self._client.get_values(self._cell_range)
        if not values:
            # An empty sheet has no header, so we cannot prove it is the right
            # sheet. Treated as unreadable rather than as "no customers exist".
            raise IntegrationError(
                "The Customers sheet is empty.",
                integration="google-sheets",
                sheet="Customers",
            )

        header, *data = values
        index = index_header(header, CUSTOMER_COLUMNS, sheet="Customers")

        parsed = [
            row
            for offset, raw in enumerate(data)
            if (
                row := parse_customer_row(
                    raw,
                    index,
                    # +2: the header is row 1 and spreadsheets are 1-indexed, so
                    # the logged number matches what a human sees in the sheet.
                    row_number=offset + 2,
                    default_country_code=self._default_country_code,
                )
            )
            is not None
        ]
        return parsed

    @staticmethod
    def _lookup_failure(exc: IntegrationError, *, phone: str) -> CustomerLookupResult:
        log.error(
            events.CUSTOMER_LOOKUP_COMPLETED,
            extra=event(
                outcome="INTEGRATION_ERROR",
                error_code=exc.code,
                caller_phone=phone,
                success=False,
            ),
        )
        return CustomerLookupResult.integration_error(failure_reason(exc))


def _values_match(expected: str, supplied: str) -> bool:
    """Compare in constant time, after forgiving how a caller says it.

    Spoken answers arrive with inconsistent spacing and case ('SW1A 1AA' vs
    'sw1a1aa'), so both sides are folded before comparison. `compare_digest`
    keeps the comparison itself from leaking the value through timing.
    """
    return hmac.compare_digest(_fold(expected), _fold(supplied))


def _fold(value: str) -> str:
    return "".join(value.split()).casefold()
