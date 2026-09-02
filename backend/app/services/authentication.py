"""The authentication flow.

    START
      -> collect phone
      -> normalise phone        (integrations/../phone.py)
      -> lookup customer        (CustomerRepository)
      -> customer found?
      -> verify identity        (CustomerRepository)
      -> AUTHENTICATED

The service owns the rules; the agent owns the wording. Each call returns an
`AuthenticationStep` saying what must happen next, so the decision of whether a
caller is authenticated is made here in Python and not in prompt text a caller
can argue with.

Two distinctions this module exists to preserve:

- **Customer-not-found is not authentication failure.** No record matched, so
  nothing was checked and no attempt was spent. The caller stays
  UNAUTHENTICATED and is offered a representative, not accused of failing.
- **An upstream failure is neither.** A timed-out spreadsheet is our problem;
  it must not consume the caller's attempt budget or end their call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum

from app.core import events
from app.core.logging import event, get_logger
from app.core.metrics import (
    AUTHENTICATION_ATTEMPTS,
    CUSTOMER_LOOKUP_LATENCY,
    CUSTOMER_LOOKUPS,
    METRICS,
)
from app.integrations.repositories import (
    CustomerLookupResult,
    CustomerRepository,
    FailureReason,
)
from app.models.enums import AuthenticationStatus, ConversationOutcome, VerificationOutcome
from app.models.session import MAX_AUTHENTICATION_ATTEMPTS, SessionState
from app.services.session_store import SessionStore

log = get_logger(__name__)


class AuthenticationStep(StrEnum):
    """What the conversation must do next.

    Instructions to the agent, not sentences for the caller — the agent phrases
    them. Keeping the decision in this vocabulary is what keeps the business
    rule out of the prompt.
    """

    PHONE_REQUIRED = "PHONE_REQUIRED"
    PHONE_NOT_UNDERSTOOD = "PHONE_NOT_UNDERSTOOD"
    CUSTOMER_NOT_FOUND = "CUSTOMER_NOT_FOUND"
    VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    ATTEMPTS_EXHAUSTED = "ATTEMPTS_EXHAUSTED"
    LOOKUP_ATTEMPTS_EXHAUSTED = "LOOKUP_ATTEMPTS_EXHAUSTED"
    AUTHENTICATED = "AUTHENTICATED"
    ALREADY_AUTHENTICATED = "ALREADY_AUTHENTICATED"
    INTEGRATION_ERROR = "INTEGRATION_ERROR"


@dataclass(frozen=True, slots=True)
class AuthenticationStepResult:
    """Outcome of one step, plus the session it produced.

    Deliberately carries no claim data. This result is the only thing the agent
    sees during authentication, so there is nothing here to leak before the
    boundary is crossed.
    """

    step: AuthenticationStep
    session: SessionState
    attempts_remaining: int = 0
    customer_name: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return self.session.is_authenticated


class AuthenticationService:
    """Drives a call from START to AUTHENTICATED, or to a safe dead end."""

    def __init__(self, customers: CustomerRepository, sessions: SessionStore) -> None:
        self._customers = customers
        self._sessions = sessions

    # --- START ------------------------------------------------------------

    async def start_call(self, call_id: str, caller_phone: str | None = None) -> SessionState:
        """Begin a call. Idempotent: a repeated start returns the live session.

        `caller_phone` is the network-provided caller ID when the platform has
        one. It is *not* treated as proof of anything — it seeds the lookup at
        best, and the caller still verifies.
        """
        existing = await self._sessions.get(call_id)
        if existing is not None:
            return existing

        session = SessionState(call_id=call_id, caller_phone=caller_phone)
        await self._sessions.save(session)
        log.info(events.CALL_STARTED, extra=event(call_id=call_id))
        return session

    # --- collect phone -> lookup customer ---------------------------------

    async def submit_phone(self, call_id: str, spoken_phone: str) -> AuthenticationStepResult:
        """Normalise the number the caller gave and look for a matching record."""
        session = await self._require_session(call_id)

        if session.is_authenticated:
            return self._result(AuthenticationStep.ALREADY_AUTHENTICATED, session)

        if session.authentication_status is AuthenticationStatus.AUTHENTICATION_FAILED:
            return self._result(AuthenticationStep.ATTEMPTS_EXHAUSTED, session)

        log.info(events.CUSTOMER_LOOKUP_STARTED, extra=event(call_id=call_id))
        started = time.perf_counter()

        result = await self._customers.lookup_customer_by_phone(spoken_phone)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        METRICS.observe(CUSTOMER_LOOKUP_LATENCY, duration_ms)

        if result.is_integration_error:
            # Our problem, not the caller's: nothing is counted against them and
            # they are certainly not told we have no record of them.
            log.warning(
                events.TOOL_ERROR, extra=event(call_id=call_id, operation="lookup_customer")
            )
            return await self._save(self._result(AuthenticationStep.INTEGRATION_ERROR, session))

        # Every readable attempt is counted, so a caller reading numbers we
        # cannot match reaches a human instead of looping.
        session = session.with_caller_phone(
            result.customer.phone_number if result.customer else spoken_phone
        )

        if result.is_found and result.customer is not None:
            session = session.with_customer_found(result.customer)
            METRICS.increment(CUSTOMER_LOOKUPS, outcome="CUSTOMER_FOUND")
            log.info(
                events.CUSTOMER_LOOKUP_COMPLETED,
                extra=event(
                    **session.log_fields(),
                    outcome="CUSTOMER_FOUND",
                    success=True,
                    duration_ms=duration_ms,
                ),
            )
            return await self._save(
                self._result(
                    AuthenticationStep.VERIFICATION_REQUIRED,
                    session,
                    customer_name=result.customer.first_name,
                )
            )

        # Not found. Distinct from failing verification: nothing was checked, so
        # the caller has failed nothing and has spent no verification attempts.
        step = _not_found_step(result, session)
        if step is not AuthenticationStep.PHONE_NOT_UNDERSTOOD:
            session = session.with_customer_not_found()

        METRICS.increment(CUSTOMER_LOOKUPS, outcome=str(result.outcome))
        log.info(
            events.CUSTOMER_LOOKUP_COMPLETED,
            extra=event(
                call_id=call_id,
                outcome=result.outcome,
                step=step,
                lookup_attempts=session.lookup_attempts,
                success=not result.is_integration_error,
                duration_ms=duration_ms,
                # Redacted by the formatter. Worth having on the failing path:
                # "which number did not match" is the first thing anyone asks.
                caller_phone=session.caller_phone,
            ),
        )
        return await self._save(self._result(step, session))

    # --- verify identity --------------------------------------------------

    async def submit_verification(
        self, call_id: str, verification_value: str
    ) -> AuthenticationStepResult:
        """Check the caller's verification value against the identified record."""
        session = await self._require_session(call_id)

        if session.is_authenticated:
            # Re-asking a verified caller to prove themselves is bad service.
            return self._result(AuthenticationStep.ALREADY_AUTHENTICATED, session)

        if session.authentication_status is AuthenticationStatus.AUTHENTICATION_FAILED:
            return self._result(AuthenticationStep.ATTEMPTS_EXHAUSTED, session)

        if session.customer_id is None:
            # Nothing to verify against — the caller has not been identified yet.
            return self._result(AuthenticationStep.PHONE_REQUIRED, session)

        if not session.can_attempt_verification:
            return self._result(AuthenticationStep.ATTEMPTS_EXHAUSTED, session)

        session = await self._save_session(session.with_verification_started())
        result = await self._customers.verify_customer(session.customer_id, verification_value)

        if result.outcome is VerificationOutcome.INTEGRATION_ERROR:
            # An unreachable upstream must not spend the caller's budget.
            session = session.with_verification_abandoned()
            log.warning(
                events.TOOL_ERROR, extra=event(call_id=call_id, operation="verify_customer")
            )
            return await self._save(self._result(AuthenticationStep.INTEGRATION_ERROR, session))

        if result.outcome is VerificationOutcome.CUSTOMER_NOT_FOUND:
            # The record disappeared between lookup and verification.
            session = session.with_verification_abandoned().with_customer_not_found()
            return await self._save(self._result(AuthenticationStep.CUSTOMER_NOT_FOUND, session))

        if result.is_verified and result.customer is not None:
            session = session.with_authenticated(result.customer)
            METRICS.increment(AUTHENTICATION_ATTEMPTS, outcome="success")
            log.info(events.AUTHENTICATION_SUCCESS, extra=event(**session.log_fields()))
            return await self._save(
                self._result(
                    AuthenticationStep.AUTHENTICATED,
                    session,
                    customer_name=result.customer.first_name,
                )
            )

        session = session.with_verification_failed()
        exhausted = session.authentication_status is AuthenticationStatus.AUTHENTICATION_FAILED
        METRICS.increment(AUTHENTICATION_ATTEMPTS, outcome="failure")
        log.warning(
            events.AUTHENTICATION_FAILED,
            extra=event(**session.log_fields(), exhausted=exhausted),
        )
        return await self._save(
            self._result(
                AuthenticationStep.ATTEMPTS_EXHAUSTED
                if exhausted
                else AuthenticationStep.VERIFICATION_FAILED,
                session,
            )
        )

    # --- escalation -------------------------------------------------------

    async def escalate(self, call_id: str, reason: str) -> SessionState:
        """Mark the call for a representative. Available in any state.

        A caller who asks for a person gets one; they are not made to finish
        authenticating first (CLAUDE.md §13).
        """
        session = await self._require_session(call_id)
        session = session.with_escalation(reason)
        await self._sessions.save(session)
        log.info(events.ESCALATION_REQUESTED, extra=event(**session.log_fields(), reason=reason))
        return session

    async def complete(self, call_id: str, outcome: ConversationOutcome) -> SessionState:
        """Record how the call ended.

        Deliberately silent: `ConversationService` owns the call lifecycle and
        emits `call.completed` with the duration. Two emitters of one event name
        means two shapes of the same record, and a dashboard that quietly
        double-counts.
        """
        session = await self._require_session(call_id)
        session = session.with_outcome(outcome)
        await self._sessions.save(session)
        return session

    # --- helpers ----------------------------------------------------------

    async def _require_session(self, call_id: str) -> SessionState:
        """Load the session, creating one if the platform skipped the start hook."""
        session = await self._sessions.get(call_id)
        if session is None:
            session = SessionState(call_id=call_id)
            await self._sessions.save(session)
            log.warning("session.recreated", extra=event(call_id=call_id))
        return session

    @staticmethod
    def _result(
        step: AuthenticationStep,
        session: SessionState,
        *,
        customer_name: str | None = None,
    ) -> AuthenticationStepResult:
        return AuthenticationStepResult(
            step=step,
            session=session,
            attempts_remaining=session.attempts_remaining,
            customer_name=customer_name,
        )

    async def _save(self, result: AuthenticationStepResult) -> AuthenticationStepResult:
        await self._sessions.save(result.session)
        return result

    async def _save_session(self, session: SessionState) -> SessionState:
        await self._sessions.save(session)
        return session


def _not_found_step(result: CustomerLookupResult, session: SessionState) -> AuthenticationStep:
    """Distinguish 'I did not catch that' from 'no account matches that number'.

    Both leave the caller unauthenticated, but they are different sentences to
    hear, and only the second is worth giving up on.
    """
    if result.reason is FailureReason.INVALID_PHONE_NUMBER:
        return AuthenticationStep.PHONE_NOT_UNDERSTOOD
    if session.lookup_attempts_remaining == 0:
        return AuthenticationStep.LOOKUP_ATTEMPTS_EXHAUSTED
    return AuthenticationStep.CUSTOMER_NOT_FOUND


__all__ = [
    "MAX_AUTHENTICATION_ATTEMPTS",
    "AuthenticationService",
    "AuthenticationStep",
    "AuthenticationStepResult",
]
