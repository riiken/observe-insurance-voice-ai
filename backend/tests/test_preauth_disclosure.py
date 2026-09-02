"""Nothing sensitive may leave the service before authentication succeeds.

CLAUDE.md §7 lists what must not be disclosed pre-authentication: claim id,
claim status, claim details, required documents, sensitive customer
information. These tests inspect everything the authentication flow produces
and assert none of it is in there.
"""

from __future__ import annotations

import pytest

from app.services.authentication import AuthenticationService
from app.services.session_store import InMemorySessionStore
from tests.session_fixtures import FakeCustomerRepository

CALL = "call-abc-123"
MARIA_PHONE = "+15550101234"
MARIA_DOB = "1985-04-12"

# Everything in the fixture data that must not appear pre-authentication.
CLAIM_SECRETS = ("CLM-88401", "CLM-88402", "UNDER_REVIEW", "DOCUMENTS_REQUIRED", "Police report")


@pytest.fixture
def auth() -> AuthenticationService:
    return AuthenticationService(FakeCustomerRepository(), InMemorySessionStore())


async def test_the_lookup_result_carries_no_claim_information(
    auth: AuthenticationService,
) -> None:
    await auth.start_call(CALL)

    result = await auth.submit_phone(CALL, MARIA_PHONE)

    rendered = repr(result)
    for secret in CLAIM_SECRETS:
        assert secret not in rendered


async def test_the_lookup_result_carries_no_verification_value(
    auth: AuthenticationService,
) -> None:
    """The secret the caller is about to be asked for must not be handed out."""
    await auth.start_call(CALL)

    result = await auth.submit_phone(CALL, MARIA_PHONE)

    assert MARIA_DOB not in repr(result)


async def test_a_failed_verification_leaks_nothing(auth: AuthenticationService) -> None:
    await auth.start_call(CALL)
    await auth.submit_phone(CALL, MARIA_PHONE)

    result = await auth.submit_verification(CALL, "1999-01-01")

    rendered = repr(result)
    assert MARIA_DOB not in rendered
    for secret in CLAIM_SECRETS:
        assert secret not in rendered


async def test_the_step_result_has_no_field_for_claim_data(
    auth: AuthenticationService,
) -> None:
    """Structural, not incidental: there is nowhere for claim data to sit."""
    await auth.start_call(CALL)

    result = await auth.submit_phone(CALL, MARIA_PHONE)

    assert set(result.__slots__) == {
        "step",
        "session",
        "attempts_remaining",
        "customer_name",
    }


async def test_only_the_first_name_is_disclosed_before_verification(
    auth: AuthenticationService,
) -> None:
    """Enough for a natural greeting, and no more than the caller already implied."""
    await auth.start_call(CALL)

    result = await auth.submit_phone(CALL, MARIA_PHONE)

    assert result.customer_name == "Maria"
    assert "Alvarez" not in str(result.customer_name)
