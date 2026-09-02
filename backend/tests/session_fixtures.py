"""Fakes for the service layer.

Repositories are faked at the *protocol*, not the HTTP transport: these tests are
about the state machine, and a fake that can be told to fail on command exercises
paths a stubbed spreadsheet cannot reach cleanly.
"""

from __future__ import annotations

from app.integrations.repositories import (
    ClaimLookupResult,
    CustomerLookupResult,
    FailureReason,
    VerificationResult,
)
from app.models.claim import Claim
from app.models.customer import Customer
from app.models.enums import ClaimStatus

MARIA = Customer(customer_id="CUST-1001", full_name="Maria Alvarez", phone_number="+15550101234")
JAMES = Customer(customer_id="CUST-1002", full_name="James Okonkwo", phone_number="+15550102345")

MARIA_CLAIM = Claim(
    claim_id="CLM-88401",
    customer_id="CUST-1001",
    status=ClaimStatus.UNDER_REVIEW,
)
JAMES_CLAIM = Claim(
    claim_id="CLM-88402",
    customer_id="CUST-1002",
    status=ClaimStatus.DOCUMENTS_REQUIRED,
    required_documents=("Police report", "Repair estimate"),
)

VERIFICATION_VALUES = {"CUST-1001": "1985-04-12", "CUST-1002": "1979-11-30"}


class FakeCustomerRepository:
    """In-memory CustomerRepository whose failures can be switched on."""

    def __init__(self, customers: list[Customer] | None = None) -> None:
        self._customers = customers if customers is not None else [MARIA, JAMES]
        self.fail_lookup_with: FailureReason | None = None
        self.fail_verify_with: FailureReason | None = None
        self.customer_vanishes = False
        self.lookup_calls = 0
        self.verify_calls = 0

    async def lookup_customer_by_phone(self, phone_number: str) -> CustomerLookupResult:
        self.lookup_calls += 1

        if self.fail_lookup_with is not None:
            return CustomerLookupResult.integration_error(self.fail_lookup_with)

        from app.core.phone import normalize_phone

        normalised = normalize_phone(phone_number)
        if normalised is None:
            return CustomerLookupResult.not_found(FailureReason.INVALID_PHONE_NUMBER)

        match = next((c for c in self._customers if c.phone_number == normalised), None)
        return CustomerLookupResult.found(match) if match else CustomerLookupResult.not_found()

    async def verify_customer(
        self, customer_id: str, verification_value: str
    ) -> VerificationResult:
        self.verify_calls += 1

        if self.fail_verify_with is not None:
            return VerificationResult.integration_error(self.fail_verify_with)
        if self.customer_vanishes:
            return VerificationResult.customer_not_found()

        match = next((c for c in self._customers if c.customer_id == customer_id), None)
        if match is None:
            return VerificationResult.customer_not_found()

        expected = VERIFICATION_VALUES.get(customer_id)
        if expected is not None and verification_value.strip() == expected:
            return VerificationResult.verified(match)
        return VerificationResult.failed()


class FakeClaimsRepository:
    """In-memory ClaimsRepository that records what it was asked for."""

    def __init__(self, claims: list[Claim] | None = None) -> None:
        self._claims = claims if claims is not None else [MARIA_CLAIM, JAMES_CLAIM]
        self.fail_with: FailureReason | None = None
        self.requested_customer_ids: list[str] = []

    async def get_claim_for_customer(self, customer_id: str) -> ClaimLookupResult:
        self.requested_customer_ids.append(customer_id)

        if self.fail_with is not None:
            return ClaimLookupResult.integration_error(self.fail_with)

        match = next((c for c in self._claims if c.customer_id == customer_id), None)
        return ClaimLookupResult.found(match) if match else ClaimLookupResult.not_found()
