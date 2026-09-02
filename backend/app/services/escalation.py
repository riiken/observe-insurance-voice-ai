"""Escalation to a human.

Creates the structured record CLAUDE.md §13 asks for — id, call, customer,
reason, timestamp, status — and marks the session. Available in any
authentication state: a caller who asks for a person gets one, and is not made
to finish verifying first.

The record carries a customer id only when the session actually established one.
An escalation from an unauthenticated call is still a valid escalation; it just
says less, which is the honest thing for it to say.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
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
    status: EscalationStatus = EscalationStatus.PENDING
    customer_id: str | None = None
    authenticated: bool = False
    notes: str | None = None

    @property
    def is_emergency(self) -> bool:
        return self.reason is EscalationReason.EMERGENCY


class EscalationService:
    """Records escalations and marks the session.

    Records are held in memory. Actually routing a call to a queue is a
    platform capability, and persisting the record belongs with the post-call
    integration — see docs/DEFERRED.md.
    """

    def __init__(self, sessions: SessionStore) -> None:
        self._sessions = sessions
        self._records: list[EscalationRecord] = []

    async def request_representative(
        self,
        call_id: str,
        reason: EscalationReason = EscalationReason.CALLER_REQUEST,
        notes: str | None = None,
    ) -> EscalationRecord:
        """Create an escalation record and flag the session."""
        session = await self._sessions.get(call_id) or SessionState(call_id=call_id)

        record = EscalationRecord(
            escalation_id=f"ESC-{uuid.uuid4().hex[:10].upper()}",
            call_id=call_id,
            reason=reason,
            created_at=datetime.now(tz=UTC),
            customer_id=session.customer_id,
            authenticated=session.is_authenticated,
            notes=notes,
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
            ),
        )
        return record

    def records_for(self, call_id: str) -> list[EscalationRecord]:
        return [record for record in self._records if record.call_id == call_id]

    @property
    def count(self) -> int:
        return len(self._records)
