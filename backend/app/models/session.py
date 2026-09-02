"""Conversation state for one call.

This object *is* the authorization boundary. `get_claim_status` is permitted
because `authentication_status is AUTHENTICATED` here — never because the model
concluded the caller sounded genuine, and never because the caller said so.

Two properties keep that true:

**It is frozen.** `session.authentication_status = AUTHENTICATED` raises
`FrozenInstanceError`. Every state change goes through a named transition on
this class, each of which requires a real result from the customer repository.
There is no assignment path, so there is no "just set the flag" shortcut for
later code to reach for under deadline.

**It never crosses the wire.** The session lives server-side, keyed by
`call_id`. Nothing in a tool call, a webhook payload or a model response is
deserialised into it, so no caller-influenced text can propose a status. The
worst a caller can do is supply a phone number and a verification value — which
is exactly the input the flow is designed to receive.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from app.models.customer import Customer
from app.models.enums import AuthenticationStatus, ConversationOutcome

# Three tries, then a representative. Enough for a mis-heard date of birth;
# few enough that guessing is not a strategy.
MAX_AUTHENTICATION_ATTEMPTS = 3

# A caller reading out numbers we cannot match should reach a human rather than
# loop forever. Separate budget from verification, and deliberately looser.
MAX_LOOKUP_ATTEMPTS = 3


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class SessionState:
    """Everything known about one call. Immutable; transitions return new states."""

    call_id: str

    caller_phone: str | None = None  # normalised E.164 once understood
    customer_id: str | None = None
    customer_name: str | None = None

    authentication_status: AuthenticationStatus = AuthenticationStatus.UNAUTHENTICATED
    authentication_attempts: int = 0
    # Counted separately: a caller reading an unknown number is not a caller
    # failing verification, and must not consume the verification budget.
    lookup_attempts: int = 0

    claim_id: str | None = None

    escalated: bool = False
    escalation_reason: str | None = None

    conversation_outcome: ConversationOutcome | None = None

    started_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    # --- authorization ----------------------------------------------------

    @property
    def is_authenticated(self) -> bool:
        """The single question every claim operation asks."""
        return self.authentication_status is AuthenticationStatus.AUTHENTICATED

    @property
    def is_terminal(self) -> bool:
        """Authentication has reached a state no further attempt can change."""
        return self.authentication_status in (
            AuthenticationStatus.AUTHENTICATED,
            AuthenticationStatus.AUTHENTICATION_FAILED,
        )

    @property
    def attempts_remaining(self) -> int:
        return max(0, MAX_AUTHENTICATION_ATTEMPTS - self.authentication_attempts)

    @property
    def lookup_attempts_remaining(self) -> int:
        return max(0, MAX_LOOKUP_ATTEMPTS - self.lookup_attempts)

    @property
    def can_attempt_verification(self) -> bool:
        """A verification attempt is only meaningful once a record is identified."""
        return (
            self.authentication_status
            in (AuthenticationStatus.CUSTOMER_FOUND, AuthenticationStatus.VERIFYING)
            and self.attempts_remaining > 0
        )

    # --- transitions ------------------------------------------------------

    def _evolve(self, **changes: object) -> SessionState:
        return replace(self, updated_at=_now(), **changes)  # type: ignore[arg-type]

    def with_caller_phone(self, phone: str) -> SessionState:
        """Record the normalised number the caller gave, and count the attempt."""
        return self._evolve(caller_phone=phone, lookup_attempts=self.lookup_attempts + 1)

    def with_customer_found(self, customer: Customer) -> SessionState:
        """A record matched. Identity is claimed, not yet proven."""
        return self._evolve(
            customer_id=customer.customer_id,
            customer_name=customer.full_name,
            authentication_status=AuthenticationStatus.CUSTOMER_FOUND,
        )

    def with_customer_not_found(self) -> SessionState:
        """No record for that number.

        Stays UNAUTHENTICATED rather than moving to AUTHENTICATION_FAILED: there
        was nothing to check against, so the caller has not failed anything and
        has spent none of their verification attempts.
        """
        return self._evolve(
            authentication_status=AuthenticationStatus.UNAUTHENTICATED,
            conversation_outcome=ConversationOutcome.CUSTOMER_NOT_FOUND,
        )

    def with_verification_started(self) -> SessionState:
        return self._evolve(authentication_status=AuthenticationStatus.VERIFYING)

    def with_verification_failed(self) -> SessionState:
        """Count one wrong answer, and fail the session once the budget is spent."""
        attempts = self.authentication_attempts + 1
        exhausted = attempts >= MAX_AUTHENTICATION_ATTEMPTS

        return self._evolve(
            authentication_attempts=attempts,
            authentication_status=(
                AuthenticationStatus.AUTHENTICATION_FAILED
                if exhausted
                else AuthenticationStatus.CUSTOMER_FOUND
            ),
            conversation_outcome=(ConversationOutcome.AUTHENTICATION_FAILED if exhausted else None),
        )

    def with_authenticated(self, customer: Customer) -> SessionState:
        """Only ever called with a verified result from the customer repository."""
        return self._evolve(
            customer_id=customer.customer_id,
            customer_name=customer.full_name,
            authentication_status=AuthenticationStatus.AUTHENTICATED,
        )

    def with_verification_abandoned(self) -> SessionState:
        """Leave VERIFYING without charging an attempt.

        Used when the check itself could not be completed — an upstream failure
        is our problem, not the caller's, and must not spend their budget.
        """
        if self.authentication_status is not AuthenticationStatus.VERIFYING:
            return self
        return self._evolve(authentication_status=AuthenticationStatus.CUSTOMER_FOUND)

    def with_claim(self, claim_id: str) -> SessionState:
        return self._evolve(claim_id=claim_id)

    def with_escalation(self, reason: str) -> SessionState:
        return self._evolve(
            escalated=True,
            escalation_reason=reason,
            conversation_outcome=ConversationOutcome.ESCALATED,
        )

    def with_outcome(self, outcome: ConversationOutcome) -> SessionState:
        return self._evolve(conversation_outcome=outcome)

    # --- observability ----------------------------------------------------

    def log_fields(self) -> dict[str, object]:
        """Safe-to-log summary. Carries no verification value and no claim detail.

        `caller_phone` is deliberately absent: the formatter redacts it, but a
        session summary has no need for it at all.
        """
        return {
            "call_id": self.call_id,
            "customer_id": self.customer_id,
            "authentication_status": self.authentication_status,
            "authentication_attempts": self.authentication_attempts,
            "escalated": self.escalated,
        }
