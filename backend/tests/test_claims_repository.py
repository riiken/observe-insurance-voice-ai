"""GoogleSheetsClaimsRepository — claim retrieval."""

from __future__ import annotations

from datetime import date

import httpx

from app.integrations.repositories import ClaimsRepository, FailureReason
from app.models.enums import ClaimLookupOutcome, ClaimStatus
from tests.sheets_fixtures import CLAIM_HEADER, claims_repository


async def test_successful_claim_lookup() -> None:
    result = await claims_repository().get_claim_for_customer("CUST-1001")

    assert result.outcome is ClaimLookupOutcome.CLAIM_FOUND
    assert result.claim is not None
    assert result.claim.claim_id == "CLM-88401"
    assert result.claim.status is ClaimStatus.UNDER_REVIEW
    assert result.claim.last_updated == date(2026, 8, 28)


async def test_documents_required_claim_lists_the_missing_documents() -> None:
    result = await claims_repository().get_claim_for_customer("CUST-1002")

    assert result.claim is not None
    assert result.claim.status is ClaimStatus.DOCUMENTS_REQUIRED
    assert result.claim.needs_documents
    assert result.claim.required_documents == ("Police report", "Repair estimate")


async def test_the_most_recently_updated_claim_wins() -> None:
    """CUST-1002 holds two claims; the current one is the one last touched."""
    result = await claims_repository().get_claim_for_customer("CUST-1002")

    assert result.claim is not None
    assert result.claim.claim_id == "CLM-88402"


async def test_a_customer_with_no_claim_is_claim_not_found() -> None:
    result = await claims_repository().get_claim_for_customer("CUST-1006")

    assert result.outcome is ClaimLookupOutcome.CLAIM_NOT_FOUND
    assert result.claim is None


async def test_empty_customer_id_returns_not_found_without_a_fetch() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not reach the network")

    result = await claims_repository(handler=handler).get_claim_for_customer("")

    assert result.outcome is ClaimLookupOutcome.CLAIM_NOT_FOUND


async def test_upstream_failure_is_never_reported_as_claim_not_found() -> None:
    result = await claims_repository(handler=lambda _r: httpx.Response(500)).get_claim_for_customer(
        "CUST-1001"
    )

    assert result.outcome is ClaimLookupOutcome.INTEGRATION_ERROR
    assert result.reason is FailureReason.UPSTREAM_ERROR


async def test_timeout_is_reported_as_a_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    result = await claims_repository(handler=handler).get_claim_for_customer("CUST-1001")

    assert result.reason is FailureReason.UPSTREAM_TIMEOUT


async def test_an_unrecognised_status_is_skipped_rather_than_guessed() -> None:
    """Reading out 'approved' for a status we do not understand is unacceptable."""
    rows = [CLAIM_HEADER, ["CLM-1", "CUST-1001", "In Arbitration", "", "2026-08-28"]]

    result = await claims_repository(rows).get_claim_for_customer("CUST-1001")

    assert result.outcome is ClaimLookupOutcome.CLAIM_NOT_FOUND


async def test_a_malformed_row_does_not_hide_the_others() -> None:
    rows = [
        CLAIM_HEADER,
        ["", "CUST-1001", "Approved", "", "2026-08-28"],
        ["CLM-2", "CUST-1001", "Submitted", "", "2026-08-29"],
    ]

    result = await claims_repository(rows).get_claim_for_customer("CUST-1001")

    assert result.claim is not None
    assert result.claim.claim_id == "CLM-2"


async def test_a_claim_with_no_readable_date_is_still_returned() -> None:
    rows = [CLAIM_HEADER, ["CLM-3", "CUST-1001", "Submitted", "", "sometime last week"]]

    result = await claims_repository(rows).get_claim_for_customer("CUST-1001")

    assert result.claim is not None
    assert result.claim.last_updated is None


async def test_a_missing_column_is_an_integration_error() -> None:
    rows = [["claim_id", "customer_id"], ["CLM-1", "CUST-1001"]]

    result = await claims_repository(rows).get_claim_for_customer("CUST-1001")

    assert result.outcome is ClaimLookupOutcome.INTEGRATION_ERROR
    assert result.reason is FailureReason.MALFORMED_DATA


async def test_repository_satisfies_the_protocol() -> None:
    assert isinstance(claims_repository(), ClaimsRepository)


async def test_health_check_does_not_raise_on_failure() -> None:
    status = await claims_repository(handler=lambda _r: httpx.Response(500)).check_health()

    assert status.healthy is False
    assert status.name == "claims"
