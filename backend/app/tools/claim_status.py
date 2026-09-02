"""The `get_claim_status` tool.

Authentication is enforced *here* as well as in the service, on purpose. The
service raises; the tool converts that into a spoken refusal so the call
continues. Two layers, one rule, and the outer one cannot accidentally weaken
the inner one — the tool has no way to authorise anything, only to phrase what
the service decided.

**On the `customer_id` argument.** It is optional and it is never trusted. The
authoritative customer is whoever the *session* authenticated as; a supplied id
is checked against that and a mismatch is refused. So the parameter exists for
an agent that wants to be explicit, but it cannot be used to aim the lookup at
somebody else's claim — which is the whole reason Phase 3 kept it off the
service signature.
"""

from __future__ import annotations

from app.core.errors import AppError, AuthorizationError
from app.core.logging import event, get_logger
from app.integrations.repositories import FailureReason
from app.models.enums import ClaimLookupOutcome, ClaimStatus
from app.schemas.claims import ClaimStatusView, SubmissionInstructionsView
from app.services.claims import ClaimsService
from app.services.guidance import ClaimGuidance
from app.services.voice import render_claim_status
from app.tools.base import ToolOutcome, ToolResult

log = get_logger(__name__)

ClaimStatusToolResult = ToolResult[ClaimStatusView]

# Fixed wording for refusals and failures. Kept out of the model's hands so a
# denial cannot be rephrased into an apology that hints at what was withheld.
_NOT_AUTHORIZED_SPEECH = "Before I can look at your claim, I need to confirm who I'm speaking with."
_NOT_FOUND_SPEECH = (
    "I can't see an open claim on your account at the moment. "
    "Would you like me to put you through to a representative?"
)
_INTEGRATION_ERROR_SPEECH = (
    "I'm having trouble reaching our claims system right now, so I can't give "
    "you an accurate update. Let me put you through to a representative."
)


class ClaimStatusTool:
    """Answers "what's happening with my claim?" for an authenticated caller."""

    name = "get_claim_status"

    def __init__(self, claims: ClaimsService, guidance: ClaimGuidance) -> None:
        self._claims = claims
        self._guidance = guidance

    async def __call__(self, call_id: str, customer_id: str | None = None) -> ClaimStatusToolResult:
        return await self.get_claim_status(call_id, customer_id)

    async def get_claim_status(
        self, call_id: str, customer_id: str | None = None
    ) -> ClaimStatusToolResult:
        """Retrieve the caller's claim, or say why it cannot be retrieved.

        Never raises for an expected condition: an unauthenticated caller, a
        missing claim and an unreachable upstream all come back as outcomes the
        agent can speak, because none of them should end a phone call.
        """
        if not call_id:
            return self._refuse(call_id="", reason="MISSING_CALL_ID")

        try:
            # A supplied customer_id is passed through to be *checked*, not
            # used: the service refuses a mismatch before any lookup happens.
            result = await self._claims.get_claim_status(
                call_id, expected_customer_id=customer_id or None
            )
        except AuthorizationError:
            # The service already logged the denial with the failing state.
            return self._refuse(call_id=call_id, reason="NOT_AUTHORIZED")
        except AppError as exc:
            return self._integration_failure(call_id, exc.code)

        if result.result.outcome is ClaimLookupOutcome.INTEGRATION_ERROR:
            reason = result.result.reason or FailureReason.UPSTREAM_ERROR
            return self._integration_failure(call_id, str(reason))

        claim = result.claim
        if claim is None:
            log.info("claim.lookup", extra=event(call_id=call_id, outcome="CLAIM_NOT_FOUND"))
            return ToolResult(
                outcome=ToolOutcome.NOT_FOUND,
                speech=_NOT_FOUND_SPEECH,
                context={"call_id": call_id},
            )

        if claim.status is ClaimStatus.DOCUMENTS_REQUIRED and not claim.required_documents:
            # The record says documents are outstanding but does not say which.
            # Naming a plausible set would be inventing a customer's obligations.
            log.warning(
                "claim.incomplete",
                extra=event(call_id=call_id, claim_id=claim.claim_id, missing="required_documents"),
            )
            return ToolResult(
                outcome=ToolOutcome.INCOMPLETE_DATA,
                speech=render_claim_status(claim, self._guidance),
                context={"call_id": call_id, "claim_id": claim.claim_id},
            )

        guidance = self._guidance.for_status(claim.status)
        view = ClaimStatusView(
            claim_id=claim.claim_id,
            status=claim.status,
            required_documents=list(claim.required_documents),
            last_updated=claim.last_updated,
            next_step=guidance.next_step,
            submission_instructions=(
                SubmissionInstructionsView(**self._guidance.submission.model_dump())
                if claim.needs_documents
                else None
            ),
        )

        return ToolResult(
            outcome=ToolOutcome.SUCCESS,
            speech=render_claim_status(claim, self._guidance),
            data=view,
            context={"call_id": call_id, "claim_id": claim.claim_id},
        )

    @staticmethod
    def _refuse(*, call_id: str, reason: str) -> ClaimStatusToolResult:
        """One refusal, identically worded whatever the reason.

        A caller probing the boundary learns nothing about which check stopped
        them, and the result carries no claim data to leak.
        """
        return ToolResult(
            outcome=ToolOutcome.NOT_AUTHORIZED,
            speech=_NOT_AUTHORIZED_SPEECH,
            context={"call_id": call_id, "reason": reason},
        )

    @staticmethod
    def _integration_failure(call_id: str, reason: str) -> ClaimStatusToolResult:
        log.error(
            "tool.error",
            extra=event(operation="get_claim_status", call_id=call_id, reason=reason),
        )
        return ToolResult(
            outcome=ToolOutcome.INTEGRATION_ERROR,
            speech=_INTEGRATION_ERROR_SPEECH,
            context={"call_id": call_id, "reason": reason},
        )
