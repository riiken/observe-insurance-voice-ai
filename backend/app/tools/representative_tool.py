"""`request_representative`.

Available in every state, at any point in the call. A caller who asks for a
person gets one; they are not routed back through the claims workflow first
(CLAUDE.md §13).

Emergencies are handled here too, and handled differently: the agent must not
pretend to be an emergency service. The response points the caller at the
service that can actually help and stops the claims conversation, rather than
carrying on troubleshooting a claim while somebody is in danger (§14).
"""

from __future__ import annotations

from app.core.logging import event, get_logger
from app.models.enums import EscalationReason
from app.services.escalation import EscalationService
from app.tools.base import ToolOutcome, ToolResult

log = get_logger(__name__)

_EMERGENCY_SPEECH = (
    "If anyone is hurt or in danger, please hang up and call emergency services "
    "on 911 right now — they can help in a way I can't. When you're safe, we'll "
    "be here to sort out the claim. I'm also flagging this for one of our team."
)

_REPRESENTATIVE_SPEECH = "Of course. I'm putting you through to a representative now — please hold."


class RequestRepresentativeTool:
    """Escalates the call to a human."""

    name = "request_representative"

    def __init__(self, escalation: EscalationService) -> None:
        self._escalation = escalation

    async def __call__(
        self,
        call_id: str,
        reason: str = EscalationReason.CALLER_REQUEST,
        notes: str | None = None,
    ) -> ToolResult:
        escalation_reason = _coerce_reason(reason)

        record = await self._escalation.request_representative(
            call_id, escalation_reason, notes=notes
        )

        return ToolResult(
            outcome=ToolOutcome.SUCCESS,
            speech=(_EMERGENCY_SPEECH if record.is_emergency else _REPRESENTATIVE_SPEECH),
            context={
                "call_id": call_id,
                "escalation_id": record.escalation_id,
                "reason": str(record.reason),
                "status": str(record.status),
            },
        )


def _coerce_reason(reason: str) -> EscalationReason:
    """Map whatever the model supplied onto the controlled vocabulary.

    An unrecognised reason still escalates — refusing to put a caller through
    because of a bad enum value would be the wrong failure — but it is recorded
    as a plain caller request rather than being trusted as a category.
    """
    try:
        return EscalationReason(str(reason).strip().upper().replace(" ", "_"))
    except ValueError:
        log.info("escalation.reason_unrecognised", extra=event(supplied=str(reason)[:40]))
        return EscalationReason.CALLER_REQUEST
