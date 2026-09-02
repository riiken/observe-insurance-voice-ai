"""Escalation to a human.

Creates the structured record CLAUDE.md §13 asks for — id, call, customer,
reason, timestamp, status — and marks the session. Available in any
authentication state: a caller who asks for a person gets one, and is not made
to finish verifying first.

The record carries a customer id only when the session actually established one.
An escalation from an unauthenticated call is still a valid escalation; it just
says less, which is the honest thing for it to say.

It carries **no claim information at all** — not the claim id, not the status,
not the documents. An escalation can be raised by an unverified caller, so
anything on the record is something an unverified caller could cause to be
written down. The session's `claim_id` is deliberately not copied across.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from app.core.logging import event, get_logger
from app.models.enums import EscalationReason, EscalationStatus
from app.models.session import SessionState
from app.services.session_store import SessionStore

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EscalationRecord:
    """A request for a human, in a shape a routing system could consume."""

    escalation_id: str
    call_id: str
    reason: EscalationReason
    created_at: datetime
    status: EscalationStatus = EscalationStatus.REQUESTED
    customer_id: str | None = None
    authenticated: bool = False
    notes: str | None = None

    @property
    def is_emergency(self) -> bool:
        return self.reason is EscalationReason.EMERGENCY


def _truncate(notes: str | None, limit: int = 200) -> str | None:
    """Bound what the model can write into a record."""
    if not notes:
        return None
    text = " ".join(str(notes).split())
    return text[:limit] if len(text) > limit else text


class EscalationService:
    """Records escalations and marks the session.

    Records are held in memory. Whether the call is actually handed over is a
    platform capability, isolated in `integrations/voice_platform.py`; this
    service only knows whether one is available, so that a record never claims
    a transfer that did not happen.
    """

    def __init__(self, sessions: SessionStore, *, transfer_available: bool = False) -> None:
        self._sessions = sessions
        self._transfer_available = transfer_available
        self._records: list[EscalationRecord] = []

    @property
    def transfer_available(self) -> bool:
        return self._transfer_available

    async def request_representative(
        self,
        call_id: str,
        reason: EscalationReason = EscalationReason.CALLER_REQUEST,
        notes: str | None = None,
    ) -> EscalationRecord:
        """Create an escalation record and flag the session.

        Always succeeds. A caller asking for a person must never be blocked by
        a failure somewhere else in the system.
        """
        session = await self._sessions.get(call_id) or SessionState(call_id=call_id)

        record = EscalationRecord(
            escalation_id=f"ESC-{uuid.uuid4().hex[:10].upper()}",
            call_id=call_id,
            reason=reason,
            created_at=datetime.now(tz=UTC),
            status=(
                EscalationStatus.TRANSFERRING
                if self._transfer_available
                else EscalationStatus.REQUESTED
            ),
            customer_id=session.customer_id,
            authenticated=session.is_authenticated,
            # Notes are the model's one-line summary. Kept for the human who
            # picks the call up; never read back to the caller.
            notes=_truncate(notes),
        )
        self._records.append(record)

        await self._sessions.save(session.with_escalation(str(reason)))

        # An emergency is logged at a level that will page someone.
        emit = log.error if record.is_emergency else log.info
        emit(
            "escalation.requested",
            extra=event(
                escalation_id=record.escalation_id,
                call_id=call_id,
                reason=reason,
                customer_id=record.customer_id,
                authenticated=record.authenticated,
                status=record.status,
            ),
        )
        return record

    async def mark_failed(self, record: EscalationRecord) -> EscalationRecord:
        """Record that a transfer we attempted did not happen."""
        log.error(
            "escalation.failed",
            extra=event(escalation_id=record.escalation_id, call_id=record.call_id),
        )
        return replace(record, status=EscalationStatus.FAILED)

    def records_for(self, call_id: str) -> list[EscalationRecord]:
        return [record for record in self._records if record.call_id == call_id]

    @property
    def count(self) -> int:
        return len(self._records)
