"""Post-call processing.

Builds the interaction record from the session and files it. Runs when the voice
platform reports the call has ended, so there is no caller left to affect — but
the guarantee still has to hold in code, because this runs inside the webhook
handler and an exception here would surface as a failed webhook.

**Nothing in this module raises.** A failure to file paperwork must never crash
or corrupt a conversation (CLAUDE.md §19). Every failure is logged and returned;
none propagates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.logging import event, get_logger
from app.integrations.repositories import (
    InteractionRepository,
    PersistOutcome,
    PersistResult,
)
from app.models.interaction import InteractionRecord
from app.models.session import SessionState
from app.services.summary import caller_name, score_sentiment, summarise

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PostCallResult:
    """What happened when we tried to file the call."""

    result: PersistResult
    record: InteractionRecord

    @property
    def persisted(self) -> bool:
        return self.result.is_persisted


class PostCallService:
    """Turns a finished session into a filed interaction record."""

    def __init__(self, interactions: InteractionRepository | None) -> None:
        # None means Integration #2 is not configured. The record is still
        # built and logged, so a deployment without the sheet still leaves a
        # trail — it just leaves it in the logs.
        self._interactions = interactions

    @property
    def configured(self) -> bool:
        return self._interactions is not None

    def build_record(self, session: SessionState) -> InteractionRecord:
        """Derive the record from observed state. No transcript, no inference."""
        return InteractionRecord(
            call_id=session.call_id,
            timestamp=datetime.now(tz=UTC),
            caller_name=caller_name(session),
            call_summary=summarise(session),
            sentiment=score_sentiment(session),
            caller_phone=session.caller_phone,
            customer_id=session.customer_id,
            claim_id=session.claim_id,
            authenticated=session.is_authenticated,
            resolution=session.conversation_outcome,
            escalated=session.escalated,
            escalation_reason=session.escalation_reason,
        )

    async def record_call(self, session: SessionState) -> PostCallResult:
        """Build and file the record. Never raises, whatever goes wrong."""
        record = self.build_record(session)

        if self._interactions is None:
            log.warning(
                "postcall.not_configured",
                extra=event(call_id=record.call_id, sentiment=record.sentiment),
            )
            return PostCallResult(PersistResult(PersistOutcome.INTEGRATION_ERROR), record)

        try:
            result = await self._interactions.save(record)
        except Exception:
            # The repository is contracted not to raise. If it does anyway,
            # that is a bug — and still not a reason to fail the webhook.
            log.exception("postcall.failed", extra=event(call_id=record.call_id))
            return PostCallResult(PersistResult(PersistOutcome.INTEGRATION_ERROR), record)

        if result.is_duplicate:
            log.info("postcall.duplicate", extra=event(call_id=record.call_id))

        return PostCallResult(result, record)
