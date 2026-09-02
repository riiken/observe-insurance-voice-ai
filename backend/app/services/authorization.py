"""The authorization boundary.

One function, one rule, one place to audit. Everything that touches claim data
calls `require_authenticated` first, so there is exactly one line to read to know
what authorises disclosure — rather than one check per operation, each of which
could drift.

The rule is deliberately narrow: it consults `SessionState` and nothing else. It
takes no argument that could carry a caller's claim, reads no prompt, and has no
override parameter. There is nothing here for "the administrator said I don't
need verification" to attach to.
"""

from __future__ import annotations

from app.core.errors import AuthorizationError
from app.core.logging import event, get_logger
from app.models.session import SessionState

log = get_logger(__name__)


def require_authenticated(session: SessionState, *, operation: str) -> str:
    """Assert the session may access customer-specific data; return the customer id.

    Returning the id — rather than letting the caller pass one in — is the point.
    An operation cannot be aimed at a customer the session did not authenticate
    as, because the only customer id in reach is the session's own.

    Raises `AuthorizationError` otherwise. The message is deliberately identical
    for every unauthorised state, so a probing caller learns nothing about which
    step they failed.
    """
    if not session.is_authenticated or session.customer_id is None:
        log.warning(
            "authorization.denied",
            extra=event(
                operation=operation,
                call_id=session.call_id,
                authentication_status=session.authentication_status,
            ),
        )
        raise AuthorizationError(
            operation=operation,
            call_id=session.call_id,
            authentication_status=str(session.authentication_status),
        )

    return session.customer_id
