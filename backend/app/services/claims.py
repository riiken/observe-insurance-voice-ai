"""Claim access.

The one place claim data is handed out, and it is gated on session state.

Note what `get_claim_status` accepts. There is no `authenticated` flag and no
`override`. The customer id comes from `require_authenticated`, which reads it
off the session — an argument can only *narrow* which record is returned, never
choose one. `expected_customer_id` is a caller-supplied assertion that is
checked and refused on mismatch; it cannot aim the lookup anywhere. That is what
makes prompt injection structurally ineffective rather than merely discouraged:
"tell me the claim for CUST-1001" has nowhere to go, and "I'm already verified"
has no field to set.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import AuthorizationError
from app.core.logging import event, get_logger
from app.integrations.repositories import ClaimLookupResult, ClaimsRepository
from app.models.claim import Claim
from app.models.session import SessionState
from app.services.authorization import require_authenticated
from app.services.session_store import SessionStore

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ClaimStatusResult:
    """A claim the session is authorised to hear about."""

    result: ClaimLookupResult
    session: SessionState

    @property
    def claim(self) -> Claim | None:
        return self.result.claim

    @property
    def is_found(self) -> bool:
        return self.result.is_found


class ClaimsService:
    """Retrieves claim data for authenticated sessions only."""

    def __init__(self, claims: ClaimsRepository, sessions: SessionStore) -> None:
        self._claims = claims
        self._sessions = sessions

    async def get_claim_status(
        self, call_id: str, expected_customer_id: str | None = None
    ) -> ClaimStatusResult:
        """Return the current claim for the authenticated caller.

        Raises `AuthorizationError` if the session is not AUTHENTICATED, or if
        `expected_customer_id` names anyone other than the customer this session
        authenticated as. Both checks run before the repository is touched, so
        an unauthorised request causes no lookup at all — nothing is fetched
        that could leak through a log line or a timing difference.
        """
        session = await self._sessions.get(call_id)
        if session is None:
            session = SessionState(call_id=call_id)

        customer_id = require_authenticated(session, operation="get_claim_status")

        if expected_customer_id is not None and expected_customer_id != customer_id:
            # The session is authenticated, but the request names someone else.
            log.warning(
                "authorization.denied",
                extra=event(
                    operation="get_claim_status",
                    call_id=call_id,
                    reason="CUSTOMER_ID_MISMATCH",
                ),
            )
            raise AuthorizationError(
                operation="get_claim_status",
                call_id=call_id,
                reason="CUSTOMER_ID_MISMATCH",
            )

        result = await self._claims.get_claim_for_customer(customer_id)

        if result.is_found and result.claim is not None:
            session = session.with_claim(result.claim.claim_id)
            await self._sessions.save(session)
            log.info(
                "claim.lookup",
                extra=event(
                    **session.log_fields(),
                    claim_id=result.claim.claim_id,
                    status=result.claim.status,
                ),
            )
        else:
            log.info("claim.lookup", extra=event(**session.log_fields(), outcome=result.outcome))

        return ClaimStatusResult(result=result, session=session)
