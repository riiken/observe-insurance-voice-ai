"""Google Sheets implementation of `InteractionRepository` (Integration #2).

Appends one row per completed call to a dedicated Interactions sheet — a
different spreadsheet from the customer and claims data, because this is the
only credential in the system with write scope and it should not be able to
edit customer records.

Idempotency is on `call_id`. The voice platform can redeliver an end-of-call
event, and a duplicate row would corrupt any reporting built on this sheet.
"""

from __future__ import annotations

import time
from collections import OrderedDict

from app.core import events
from app.core.errors import IntegrationError
from app.core.logging import event, get_logger
from app.core.metrics import METRICS, POSTCALL_LATENCY
from app.integrations.base import DependencyStatus
from app.integrations.repositories import PersistResult
from app.integrations.sheets.client import GoogleSheetsClient
from app.integrations.sheets.failures import failure_reason
from app.integrations.sheets.rows import index_header
from app.models.interaction import INTERACTION_COLUMNS, InteractionRecord

log = get_logger(__name__)


class GoogleSheetsInteractionRepository:
    """Appends interaction records, once per call."""

    name = "interactions"

    def __init__(
        self,
        client: GoogleSheetsClient,
        *,
        cell_range: str = "Interactions!A:L",
        recorded_limit: int = 10_000,
    ) -> None:
        self._client = client
        self._cell_range = cell_range
        # In-process guard. The sheet is still checked, but this catches the
        # common case — a retry arriving seconds later — without a round trip,
        # and closes the window between our read and our write within a process.
        #
        # Bounded: an unbounded set grows for the lifetime of the process. It is
        # a hint, not the source of truth — evicting an old call id costs one
        # extra read, and the sheet still prevents the duplicate.
        self._recorded: OrderedDict[str, None] = OrderedDict()
        self._recorded_limit = recorded_limit

    async def save(self, record: InteractionRecord) -> PersistResult:
        """Append the record unless this call is already on file."""
        if record.call_id in self._recorded:
            log.info(
                events.POSTCALL_DUPLICATE,
                extra=event(call_id=record.call_id, source="memory"),
            )
            return PersistResult.already_recorded()

        started = time.perf_counter()

        try:
            if await self._already_recorded(record.call_id):
                self._remember(record.call_id)
                log.info(
                    events.POSTCALL_DUPLICATE,
                    extra=event(call_id=record.call_id, source="sheet"),
                )
                return PersistResult.already_recorded()

            await self._client.append_values(self._cell_range, [record.as_row()])
        except IntegrationError as exc:
            log.error(
                events.POSTCALL_FAILED,
                extra=event(call_id=record.call_id, error_code=exc.code, success=False),
            )
            return PersistResult.integration_error(failure_reason(exc))

        # Marked only after the write is confirmed, so a failed attempt can be
        # retried rather than being mistaken for a duplicate.
        self._remember(record.call_id)
        METRICS.observe(POSTCALL_LATENCY, (time.perf_counter() - started) * 1000)
        log.info(
            events.POSTCALL_PERSISTED,
            extra=event(
                call_id=record.call_id,
                sentiment=record.sentiment,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                success=True,
            ),
        )
        return PersistResult.persisted()

    def _remember(self, call_id: str) -> None:
        """Note a filed call, evicting the oldest once the cache is full."""
        self._recorded[call_id] = None
        while len(self._recorded) > self._recorded_limit:
            self._recorded.popitem(last=False)

    async def _already_recorded(self, call_id: str) -> bool:
        """Is this call already on the sheet?

        Reads the whole sheet, which is fine at demo scale and once per call.
        A malformed sheet raises rather than returning False: writing a second
        row because we could not read the first is the failure this check
        exists to prevent.
        """
        values = await self._client.get_values(self._cell_range)
        if not values:
            # An empty sheet has no header, so we cannot prove it is the right
            # one. Treated as unreadable rather than as "no records yet".
            raise IntegrationError(
                "The Interactions sheet is empty.",
                integration="google-sheets",
                sheet="Interactions",
            )

        header, *rows = values
        index = index_header(header, ("call_id",), sheet="Interactions")
        position = index["call_id"]

        return any(len(row) > position and row[position].strip() == call_id for row in rows)

    async def check_health(self) -> DependencyStatus:
        """Readiness: can we read the sheet, and does it have our columns?"""
        started = time.perf_counter()
        try:
            values = await self._client.get_values(self._cell_range)
            if not values:
                raise IntegrationError(
                    "The Interactions sheet is empty.",
                    integration="google-sheets",
                    sheet="Interactions",
                )
            index_header(values[0], INTERACTION_COLUMNS, sheet="Interactions")
        except IntegrationError as exc:
            return DependencyStatus(self.name, healthy=False, detail=exc.code)

        return DependencyStatus(
            self.name,
            healthy=True,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
