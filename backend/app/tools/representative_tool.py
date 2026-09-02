"""`request_representative`, and the emergency response.

Available in every state, at any point in the call. A caller who asks for a
person gets one; they are not routed back through the claims workflow first, and
they are not asked to justify it (CLAUDE.md §13).

Emergencies are handled here too, and handled differently: the agent must not
pretend to be an emergency service. The response points the caller at the
service that can actually help and stops the claims conversation, rather than
carrying on troubleshooting a claim while somebody is in danger (§14).

The emergency reason is not taken on trust from the model. `SafetyService` reads
the caller's own words as well, so an emergency the model failed to flag still
gets the emergency response.
"""

from __future__ import annotations

from app.core.logging import event, get_logger
from app.models.enums import EscalationReason
from app.schemas.escalation import EscalationView
from app.services.escalation import EscalationRecord, EscalationService
from app.services.safety import SafetyService
from app.tools.base import ToolOutcome, ToolResult

log = get_logger(__name__)

RepresentativeToolResult = ToolResult[EscalationView]

# Fixed wording. Safety-critical text is not left to the model to compose.
EMERGENCY_SPEECH = (
    "If anyone is hurt or in danger, please hang up and call emergency services "
    "on 911 right now — they can help in a way I can't. When you're safe, we'll "
    "be here to sort out the claim. I'm flagging this for our team as well."
)

_TRANSFERRING_SPEECH = "Of course. I'm putting you through to a representative now — please hold."

_REQUESTED_SPEECH = (
    "Of course. I've passed this to our team and a representative will pick it "
    "up — please hold while I hand you over."
)


class RequestRepresentativeTool:
    """Escalates the call to a human, and handles emergencies."""

    name = "request_representative"

    def __init__(
        self,
        escalation: EscalationService,
        safety: SafetyService,
        *,
        transfer_to: str | None = None,
    ) -> None:
        self._escalation = escalation
        self._safety = safety
        self._transfer_to = transfer_to

    async def __call__(
        self,
        call_id: str,
        reason: str = EscalationReason.CALLER_REQUEST,
        notes: str | None = None,
    ) -> RepresentativeToolResult:
        escalation_reason = _coerce_reason(reason)

        # The model said this is an emergency, or the caller's own words say so.
        # Either is enough — a missed emergency is the failure that matters.
        if escalation_reason is not EscalationReason.EMERGENCY and (
            self._safety.assess(notes).is_emergency
        ):
            log.warning(
                "safety.reason_upgraded",
                extra=event(call_id=call_id, supplied_reason=str(escalation_reason)),
            )
            escalation_reason = EscalationReason.EMERGENCY

        record = await self._escalation.request_representative(
            call_id, escalation_reason, notes=notes
        )

        return build_result(record, transfer_to=self._transfer_to)


def build_result(
    record: EscalationRecord, *, transfer_to: str | None = None
) -> RepresentativeToolResult:
    """Turn an escalation record into what the caller hears.

    Shared with the safety interceptor so an emergency raised by detection and
    one raised by the model produce exactly the same response.

    The view carries no claim information — an escalation can be raised by an
    unverified caller, so nothing here may be claim data (CLAUDE.md §7).
    """
    if record.is_emergency:
        speech = EMERGENCY_SPEECH
    elif transfer_to:
        speech = _TRANSFERRING_SPEECH
    else:
        speech = _REQUESTED_SPEECH

    view = EscalationView(
        escalation_id=record.escalation_id,
        reason=record.reason,
        status=record.status,
        created_at=record.created_at,
        is_emergency=record.is_emergency,
        transfer_available=bool(transfer_to),
    )

    return ToolResult(
        outcome=ToolOutcome.SUCCESS,
        speech=speech,
        data=view,
        context={
            "call_id": record.call_id,
            "escalation_id": record.escalation_id,
            "reason": str(record.reason),
            "status": str(record.status),
        },
        transfer_to=transfer_to,
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
