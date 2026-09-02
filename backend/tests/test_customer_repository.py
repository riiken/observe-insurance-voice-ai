"""GoogleSheetsCustomerRepository — lookup and verification.

Everything external is mocked; no Google credentials are involved.
"""

from __future__ import annotations

import httpx
import pytest

from app.integrations.repositories import CustomerRepository, FailureReason
from app.models.enums import CustomerLookupOutcome, VerificationOutcome
from tests.sheets_fixtures import (
    CUSTOMER_HEADER,
    customer_repository,
)

# --- lookup ------------------------------------------------------------------


async def test_successful_lookup_returns_the_customer() -> None:
    result = await customer_repository().lookup_customer_by_phone("+15550101234")

    assert result.outcome is CustomerLookupOutcome.CUSTOMER_FOUND
    assert result.is_found
    assert result.customer is not None
    assert result.customer.customer_id == "CUST-1001"
    assert result.customer.full_name == "Maria Alvarez"


@pytest.mark.parametrize(
    "spoken", ["+15550101234", "555-010-1234", "(555) 010 1234", "1 555 010 1234"]
)
async def test_lookup_normalises_the_caller_number(spoken: str) -> None:
    """The sheet stores '+1 555 010 1234'; callers say it every other way."""
    result = await customer_repository().lookup_customer_by_phone(spoken)

    assert result.is_found
    assert result.customer is not None
    assert result.customer.customer_id == "CUST-1001"


async def test_lookup_normalises_the_stored_number_too() -> None:
    rows = [CUSTOMER_HEADER, ["CUST-2001", "Sam Reed", "(555) 010-7777 ", "1980-01-01"]]

    result = await customer_repository(rows).lookup_customer_by_phone("+15550107777")

    assert result.is_found


async def test_unknown_number_is_customer_not_found() -> None:
    result = await customer_repository().lookup_customer_by_phone("+15550109999")

    assert result.outcome is CustomerLookupOutcome.CUSTOMER_NOT_FOUND
    assert result.reason is FailureReason.NO_MATCHING_RECORD
    assert result.customer is None


async def test_invalid_phone_number_is_reported_distinctly() -> None:
    """'I didn't catch that number' is a different sentence to 'you have no account'."""
    result = await customer_repository().lookup_customer_by_phone("not a number")

    assert result.outcome is CustomerLookupOutcome.CUSTOMER_NOT_FOUND
    assert result.reason is FailureReason.INVALID_PHONE_NUMBER


async def test_customer_lookup_never_exposes_the_verification_value() -> None:
    result = await customer_repository().lookup_customer_by_phone("+15550101234")

    assert result.customer is not None
    assert "1985-04-12" not in repr(result)
    assert not hasattr(result.customer, "verification_value")


async def test_customer_lookup_never_exposes_claim_information() -> None:
    """Lookup happens before authentication, so everything it returns is disclosable.

    Asserted against the field list rather than specific values, so adding a
    claim field to Customer later fails here loudly instead of quietly widening
    what an unauthenticated caller can be told.
    """
    result = await customer_repository().lookup_customer_by_phone("+15550101234")

    assert result.customer is not None
    assert list(result.customer.__slots__) == ["customer_id", "full_name", "phone_number"]


# --- malformed data ----------------------------------------------------------


async def test_a_malformed_row_is_skipped_and_the_others_still_work() -> None:
    rows = [
        CUSTOMER_HEADER,
        ["CUST-9001", "", "+1 555 010 8888", "1980-01-01"],  # no name
        ["", "Nina Ortiz", "+1 555 010 9001", "1980-01-01"],  # no id
        ["CUST-9003", "Ada Byron", "", "1980-01-01"],  # no phone
        ["CUST-9004", "Grace Hopper", "+1 555 010 9004", ""],  # no verification value
        ["CUST-9005", "Alan Turing", "banana", "1980-01-01"],  # unusable phone
        ["CUST-1001", "Maria Alvarez", "+1 555 010 1234", "1985-04-12"],  # good
    ]

    result = await customer_repository(rows).lookup_customer_by_phone("+15550101234")

    assert result.is_found
    assert result.customer is not None
    assert result.customer.customer_id == "CUST-1001"


async def test_short_rows_are_tolerated() -> None:
    """Sheets omits trailing empty cells entirely."""
    rows = [CUSTOMER_HEADER, ["CUST-9006", "Ben Ako", "+1 555 010 9006"]]

    result = await customer_repository(rows).lookup_customer_by_phone("+15550109006")

    assert result.outcome is CustomerLookupOutcome.CUSTOMER_NOT_FOUND


async def test_column_order_does_not_matter() -> None:
    rows = [
        ["phone_number", "verification_value", "customer_id", "full_name"],
        ["+1 555 010 1234", "1985-04-12", "CUST-1001", "Maria Alvarez"],
    ]

    result = await customer_repository(rows).lookup_customer_by_phone("+15550101234")

    assert result.is_found


async def test_a_missing_column_is_an_integration_error_not_a_missing_customer() -> None:
    """We cannot read the sheet, so we must not claim the customer is absent."""
    rows = [["customer_id", "full_name"], ["CUST-1001", "Maria Alvarez"]]

    result = await customer_repository(rows).lookup_customer_by_phone("+15550101234")

    assert result.outcome is CustomerLookupOutcome.INTEGRATION_ERROR
    assert result.reason is FailureReason.MALFORMED_DATA


async def test_an_empty_sheet_is_an_integration_error() -> None:
    result = await customer_repository([]).lookup_customer_by_phone("+15550101234")

    assert result.outcome is CustomerLookupOutcome.INTEGRATION_ERROR


# --- integration failure -----------------------------------------------------


async def test_upstream_failure_is_never_reported_as_customer_not_found() -> None:
    """The single most important behaviour in this module (CLAUDE.md §10)."""
    repository = customer_repository(handler=lambda _r: httpx.Response(500))

    result = await repository.lookup_customer_by_phone("+15550101234")

    assert result.outcome is CustomerLookupOutcome.INTEGRATION_ERROR
    assert result.outcome is not CustomerLookupOutcome.CUSTOMER_NOT_FOUND
    assert result.reason is FailureReason.UPSTREAM_ERROR
    assert result.customer is None


async def test_timeout_is_reported_as_a_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    result = await customer_repository(handler=handler).lookup_customer_by_phone("+15550101234")

    assert result.outcome is CustomerLookupOutcome.INTEGRATION_ERROR
    assert result.reason is FailureReason.UPSTREAM_TIMEOUT


# --- verification ------------------------------------------------------------


async def test_successful_verification_returns_the_customer() -> None:
    result = await customer_repository().verify_customer("CUST-1001", "1985-04-12")

    assert result.outcome is VerificationOutcome.VERIFIED
    assert result.is_verified
    assert result.customer is not None
    assert result.customer.full_name == "Maria Alvarez"


@pytest.mark.parametrize("spoken", ["1985-04-12", " 1985-04-12 ", "1985-04-12\n"])
async def test_verification_tolerates_spacing(spoken: str) -> None:
    assert (await customer_repository().verify_customer("CUST-1001", spoken)).is_verified


async def test_wrong_value_fails_verification() -> None:
    result = await customer_repository().verify_customer("CUST-1001", "1999-01-01")

    assert result.outcome is VerificationOutcome.VERIFICATION_FAILED
    assert not result.is_verified
    assert result.customer is None


@pytest.mark.parametrize("supplied", ["", "   "])
async def test_an_empty_answer_fails_rather_than_erroring(supplied: str) -> None:
    result = await customer_repository().verify_customer("CUST-1001", supplied)

    assert result.outcome is VerificationOutcome.VERIFICATION_FAILED


async def test_verification_of_an_unknown_customer_is_not_a_failed_attempt() -> None:
    """Only a wrong answer should count against the caller's retry budget."""
    result = await customer_repository().verify_customer("CUST-0000", "1985-04-12")

    assert result.outcome is VerificationOutcome.CUSTOMER_NOT_FOUND


async def test_verification_upstream_failure_is_not_a_failed_attempt() -> None:
    repository = customer_repository(handler=lambda _r: httpx.Response(503))

    result = await repository.verify_customer("CUST-1001", "1985-04-12")

    assert result.outcome is VerificationOutcome.INTEGRATION_ERROR
    assert result.outcome is not VerificationOutcome.VERIFICATION_FAILED


async def test_verification_result_never_echoes_the_expected_value() -> None:
    result = await customer_repository().verify_customer("CUST-1001", "1999-01-01")

    assert "1985-04-12" not in repr(result)


# --- contract ----------------------------------------------------------------


async def test_repository_satisfies_the_protocol() -> None:
    assert isinstance(customer_repository(), CustomerRepository)


async def test_health_check_reports_unhealthy_without_raising() -> None:
    repository = customer_repository(handler=lambda _r: httpx.Response(500))

    status = await repository.check_health()

    assert status.healthy is False
    assert status.name == "customers"


async def test_health_check_reports_healthy_when_the_sheet_reads() -> None:
    status = await customer_repository().check_health()

    assert status.healthy is True
