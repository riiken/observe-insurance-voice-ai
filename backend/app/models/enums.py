"""Domain vocabulary shared across services, tools and integrations.

These are controlled values the whole system agrees on: outcomes are compared
as enum members, never as free text, so a typo cannot silently turn a failure
into a success.
"""

from __future__ import annotations

from enum import StrEnum


class AuthenticationStatus(StrEnum):
    """Explicit session authentication state — the authorization source of truth.

    Authorization is decided by this value alone, never by what the model
    believes about the conversation. Nothing a caller says and nothing the model
    infers can move the session between these states: only a real verification
    result from the customer repository can.

    States, and what each one means for claim access:

    - `UNAUTHENTICATED` — start of call, and where a caller stays when no
      customer matched their number. Explicitly *not* the same as
      `AUTHENTICATION_FAILED`: we have no record to check against, so nothing
      has been got wrong yet and the caller has spent no attempts.
    - `CUSTOMER_FOUND` — a record matched the number. Identity is claimed, not
      proven; discloses nothing.
    - `VERIFYING` — a verification value is being checked upstream.
    - `AUTHENTICATED` — terminal, and the only state that authorises claim
      access.
    - `AUTHENTICATION_FAILED` — terminal. The attempt budget is spent.
    """

    UNAUTHENTICATED = "UNAUTHENTICATED"
    CUSTOMER_FOUND = "CUSTOMER_FOUND"
    VERIFYING = "VERIFYING"
    AUTHENTICATED = "AUTHENTICATED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"


class CustomerLookupOutcome(StrEnum):
    """A missing customer and a broken integration are never the same thing."""

    CUSTOMER_FOUND = "CUSTOMER_FOUND"
    CUSTOMER_NOT_FOUND = "CUSTOMER_NOT_FOUND"
    INTEGRATION_ERROR = "INTEGRATION_ERROR"


class VerificationOutcome(StrEnum):
    """Result of checking a caller's verification value.

    `CUSTOMER_NOT_FOUND` and `INTEGRATION_ERROR` are kept separate from
    `VERIFICATION_FAILED`: only the last one is the caller getting it wrong, and
    only that one should count against their retry budget.
    """

    VERIFIED = "VERIFIED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    CUSTOMER_NOT_FOUND = "CUSTOMER_NOT_FOUND"
    INTEGRATION_ERROR = "INTEGRATION_ERROR"


class ClaimLookupOutcome(StrEnum):
    """As with customers, a missing claim is never an upstream failure."""

    CLAIM_FOUND = "CLAIM_FOUND"
    CLAIM_NOT_FOUND = "CLAIM_NOT_FOUND"
    INTEGRATION_ERROR = "INTEGRATION_ERROR"


class ClaimStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DOCUMENTS_REQUIRED = "DOCUMENTS_REQUIRED"


class Sentiment(StrEnum):
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"


class ConversationOutcome(StrEnum):
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    CUSTOMER_NOT_FOUND = "CUSTOMER_NOT_FOUND"
    EMERGENCY = "EMERGENCY"
    ABANDONED = "ABANDONED"
