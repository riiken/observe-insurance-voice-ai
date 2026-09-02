"""Repository contracts for customer and claim retrieval (Integration #1).

Business logic depends on *these* protocols, never on Google Sheets. Replacing
the sheet with a real policy-administration API should touch only the adapters
under `integrations/sheets/`, and no service, tool or agent.

Every operation returns a structured result carrying an explicit outcome rather
than raising for expected conditions. That is deliberate: a phone call must not
end because an upstream is slow, and — the rule this whole module exists to
enforce — an integration failure must never be indistinguishable from
"no such customer" (CLAUDE.md §10). A caller who *does* have a policy must never
be told we have no record of them because a spreadsheet was unreachable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.models.claim import Claim
from app.models.customer import Customer
from app.models.enums import ClaimLookupOutcome, CustomerLookupOutcome, VerificationOutcome
from app.models.interaction import InteractionRecord


class FailureReason(StrEnum):
    """Why a lookup did not return a record.

    Additional detail *underneath* the mandated outcome vocabulary, not a
    replacement for it. It lets the agent say "I didn't catch that number"
    instead of "you have no account" — a materially different sentence to hear
    on a support line — without blurring the three outcomes CLAUDE.md requires.

    Every value is safe to log and safe to act on. None of them is safe to read
    aloud verbatim; the agent phrases them.
    """

    INVALID_PHONE_NUMBER = "INVALID_PHONE_NUMBER"
    NO_MATCHING_RECORD = "NO_MATCHING_RECORD"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    MALFORMED_DATA = "MALFORMED_DATA"
    NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass(frozen=True, slots=True)
class CustomerLookupResult:
    """Outcome of `lookup_customer_by_phone`."""

    outcome: CustomerLookupOutcome
    customer: Customer | None = None
    reason: FailureReason | None = None

    @property
    def is_found(self) -> bool:
        return self.outcome is CustomerLookupOutcome.CUSTOMER_FOUND

    @property
    def is_integration_error(self) -> bool:
        return self.outcome is CustomerLookupOutcome.INTEGRATION_ERROR

    @classmethod
    def found(cls, customer: Customer) -> CustomerLookupResult:
        return cls(CustomerLookupOutcome.CUSTOMER_FOUND, customer=customer)

    @classmethod
    def not_found(
        cls, reason: FailureReason = FailureReason.NO_MATCHING_RECORD
    ) -> CustomerLookupResult:
        return cls(CustomerLookupOutcome.CUSTOMER_NOT_FOUND, reason=reason)

    @classmethod
    def integration_error(cls, reason: FailureReason) -> CustomerLookupResult:
        return cls(CustomerLookupOutcome.INTEGRATION_ERROR, reason=reason)


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Outcome of `verify_customer`.

    Carries no echo of the submitted or expected value.
    """

    outcome: VerificationOutcome
    customer: Customer | None = None
    reason: FailureReason | None = None

    @property
    def is_verified(self) -> bool:
        return self.outcome is VerificationOutcome.VERIFIED

    @classmethod
    def verified(cls, customer: Customer) -> VerificationResult:
        return cls(VerificationOutcome.VERIFIED, customer=customer)

    @classmethod
    def failed(cls) -> VerificationResult:
        return cls(VerificationOutcome.VERIFICATION_FAILED)

    @classmethod
    def customer_not_found(cls) -> VerificationResult:
        return cls(VerificationOutcome.CUSTOMER_NOT_FOUND, reason=FailureReason.NO_MATCHING_RECORD)

    @classmethod
    def integration_error(cls, reason: FailureReason) -> VerificationResult:
        return cls(VerificationOutcome.INTEGRATION_ERROR, reason=reason)


@dataclass(frozen=True, slots=True)
class ClaimLookupResult:
    """Outcome of `get_claim_for_customer`."""

    outcome: ClaimLookupOutcome
    claim: Claim | None = None
    reason: FailureReason | None = None

    @property
    def is_found(self) -> bool:
        return self.outcome is ClaimLookupOutcome.CLAIM_FOUND

    @property
    def is_integration_error(self) -> bool:
        return self.outcome is ClaimLookupOutcome.INTEGRATION_ERROR

    @classmethod
    def found(cls, claim: Claim) -> ClaimLookupResult:
        return cls(ClaimLookupOutcome.CLAIM_FOUND, claim=claim)

    @classmethod
    def not_found(cls) -> ClaimLookupResult:
        return cls(ClaimLookupOutcome.CLAIM_NOT_FOUND, reason=FailureReason.NO_MATCHING_RECORD)

    @classmethod
    def integration_error(cls, reason: FailureReason) -> ClaimLookupResult:
        return cls(ClaimLookupOutcome.INTEGRATION_ERROR, reason=reason)


@runtime_checkable
class CustomerRepository(Protocol):
    """Read access to customer records."""

    async def lookup_customer_by_phone(self, phone_number: str) -> CustomerLookupResult:
        """Find the customer holding `phone_number`.

        `phone_number` arrives in whatever shape the caller said it; the
        implementation normalises before matching.
        """
        ...

    async def verify_customer(
        self, customer_id: str, verification_value: str
    ) -> VerificationResult:
        """Check a caller-supplied verification value against the record.

        The expected value never leaves the implementation — not in the result,
        not in a log line, not in an exception.
        """
        ...


class PersistOutcome(StrEnum):
    """Result of writing a post-call record."""

    PERSISTED = "PERSISTED"
    # The call_id was already on file. Not an error — the expected answer to a
    # redelivered webhook.
    ALREADY_RECORDED = "ALREADY_RECORDED"
    INTEGRATION_ERROR = "INTEGRATION_ERROR"


@dataclass(frozen=True, slots=True)
class PersistResult:
    """Outcome of `save`. Never raises for an expected condition."""

    outcome: PersistOutcome
    reason: FailureReason | None = None

    @property
    def is_persisted(self) -> bool:
        return self.outcome is PersistOutcome.PERSISTED

    @property
    def is_duplicate(self) -> bool:
        return self.outcome is PersistOutcome.ALREADY_RECORDED

    @classmethod
    def persisted(cls) -> PersistResult:
        return cls(PersistOutcome.PERSISTED)

    @classmethod
    def already_recorded(cls) -> PersistResult:
        return cls(PersistOutcome.ALREADY_RECORDED)

    @classmethod
    def integration_error(cls, reason: FailureReason) -> PersistResult:
        return cls(PersistOutcome.INTEGRATION_ERROR, reason=reason)


@runtime_checkable
class InteractionRepository(Protocol):
    """Write access to the post-call interaction log (Integration #2).

    Deliberately separate from the customer and claims repositories: it is a
    different sheet, a different credential, and the only place the system
    writes anything. Keeping the write path narrow means one place to audit.
    """

    async def save(self, record: InteractionRecord) -> PersistResult:
        """Persist one interaction record.

        Must be idempotent on `record.call_id`: a redelivered end-of-call event
        must not produce a second row. Must not raise — a failure to file
        paperwork cannot be allowed to affect a caller.
        """
        ...


@runtime_checkable
class ClaimsRepository(Protocol):
    """Read access to claim records.

    Nothing here enforces authentication; that is the caller's job and lives in
    the claims service (Phase 3). Keeping the boundary out of the repository
    means there is exactly one place it can be got wrong, rather than one per
    data source.
    """

    async def get_claim_for_customer(self, customer_id: str) -> ClaimLookupResult:
        """Return the customer's current claim, if they have one."""
        ...
