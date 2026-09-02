"""Domain vocabulary shared across services, tools and integrations.

These are controlled values the whole system agrees on. They live here in
Phase 1 because the log/record shapes and the readiness contract already refer
to them; the workflow that produces them arrives in Phase 2.
"""

from __future__ import annotations

from enum import StrEnum


class AuthenticationStatus(StrEnum):
    """Explicit session authentication state — the authorization source of truth.

    Authorization is decided by this value alone, never by what the model
    believes about the conversation.
    """

    UNAUTHENTICATED = "UNAUTHENTICATED"
    PENDING = "PENDING"
    AUTHENTICATED = "AUTHENTICATED"
    FAILED = "FAILED"


class CustomerLookupOutcome(StrEnum):
    """A missing customer and a broken integration are never the same thing."""

    CUSTOMER_FOUND = "CUSTOMER_FOUND"
    CUSTOMER_NOT_FOUND = "CUSTOMER_NOT_FOUND"
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
