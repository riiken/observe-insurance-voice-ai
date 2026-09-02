"""The authentication flow: START -> phone -> lookup -> verify -> AUTHENTICATED."""

from __future__ import annotations

import pytest

from app.integrations.repositories import FailureReason
from app.models.enums import AuthenticationStatus, ConversationOutcome
from app.models.session import MAX_AUTHENTICATION_ATTEMPTS
from app.services.authentication import AuthenticationService, AuthenticationStep
from app.services.session_store import InMemorySessionStore
from tests.session_fixtures import FakeCustomerRepository

CALL = "call-abc-123"
MARIA_PHONE = "+15550101234"
MARIA_DOB = "1985-04-12"


@pytest.fixture
def customers() -> FakeCustomerRepository:
    return FakeCustomerRepository()


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def auth(customers: FakeCustomerRepository, store: InMemorySessionStore) -> AuthenticationService:
    return AuthenticationService(customers, store)


async def _identified(auth: AuthenticationService, phone: str = MARIA_PHONE) -> None:
    await auth.start_call(CALL)
    await auth.submit_phone(CALL, phone)


# --- START --------------------------------------------------------------------


async def test_a_call_starts_unauthenticated(auth: AuthenticationService) -> None:
    session = await auth.start_call(CALL)

    assert session.call_id == CALL
    assert session.authentication_status is AuthenticationStatus.UNAUTHENTICATED
    assert session.is_authenticated is False


async def test_starting_twice_returns_the_same_session(auth: AuthenticationService) -> None:
    """A platform re-sending the start hook must not reset progress."""
    await _identified(auth)

    session = await auth.start_call(CALL)

    assert session.authentication_status is AuthenticationStatus.CUSTOMER_FOUND


async def test_network_caller_id_is_not_treated_as_proof(auth: AuthenticationService) -> None:
    """A caller ID seeds the lookup at best; it authenticates nobody."""
    session = await auth.start_call(CALL, caller_phone=MARIA_PHONE)

    assert session.caller_phone == MARIA_PHONE
    assert session.is_authenticated is False
    assert session.authentication_status is AuthenticationStatus.UNAUTHENTICATED


# --- valid authentication -----------------------------------------------------


async def test_the_happy_path(auth: AuthenticationService) -> None:
    await auth.start_call(CALL)

    lookup = await auth.submit_phone(CALL, MARIA_PHONE)
    assert lookup.step is AuthenticationStep.VERIFICATION_REQUIRED
    assert lookup.session.authentication_status is AuthenticationStatus.CUSTOMER_FOUND
    assert lookup.customer_name == "Maria"
    assert lookup.is_authenticated is False

    verified = await auth.submit_verification(CALL, MARIA_DOB)
    assert verified.step is AuthenticationStep.AUTHENTICATED
    assert verified.session.authentication_status is AuthenticationStatus.AUTHENTICATED
    assert verified.is_authenticated is True
    assert verified.session.customer_id == "CUST-1001"


@pytest.mark.parametrize("spoken", ["+15550101234", "555-010-1234", "(555) 010 1234"])
async def test_the_number_is_normalised_before_lookup(
    auth: AuthenticationService, spoken: str
) -> None:
    await auth.start_call(CALL)

    result = await auth.submit_phone(CALL, spoken)

    assert result.step is AuthenticationStep.VERIFICATION_REQUIRED
    assert result.session.caller_phone == MARIA_PHONE


async def test_verification_tolerates_spacing(auth: AuthenticationService) -> None:
    await _identified(auth)

    assert (await auth.submit_verification(CALL, f"  {MARIA_DOB} ")).is_authenticated


async def test_state_is_persisted_between_turns(
    auth: AuthenticationService, store: InMemorySessionStore
) -> None:
    await _identified(auth)
    await auth.submit_verification(CALL, MARIA_DOB)

    stored = await store.get(CALL)

    assert stored is not None
    assert stored.is_authenticated is True


async def test_an_authenticated_caller_is_not_asked_again(
    auth: AuthenticationService, customers: FakeCustomerRepository
) -> None:
    """Repetitive authentication is a bad call experience (CLAUDE.md §16)."""
    await _identified(auth)
    await auth.submit_verification(CALL, MARIA_DOB)
    calls_before = customers.verify_calls

    again = await auth.submit_verification(CALL, MARIA_DOB)

    assert again.step is AuthenticationStep.ALREADY_AUTHENTICATED
    assert again.is_authenticated is True
    assert customers.verify_calls == calls_before  # no second upstream call


# --- incorrect verification ---------------------------------------------------


async def test_a_wrong_value_fails_without_authenticating(auth: AuthenticationService) -> None:
    await _identified(auth)

    result = await auth.submit_verification(CALL, "1999-01-01")

    assert result.step is AuthenticationStep.VERIFICATION_FAILED
    assert result.is_authenticated is False
    assert result.session.authentication_attempts == 1
    assert result.attempts_remaining == MAX_AUTHENTICATION_ATTEMPTS - 1
    assert result.session.authentication_status is AuthenticationStatus.CUSTOMER_FOUND


async def test_a_wrong_answer_can_be_followed_by_a_right_one(
    auth: AuthenticationService,
) -> None:
    await _identified(auth)
    await auth.submit_verification(CALL, "1999-01-01")

    result = await auth.submit_verification(CALL, MARIA_DOB)

    assert result.is_authenticated is True
    assert result.session.authentication_attempts == 1  # the failure still counted


async def test_repeated_wrong_answers_accumulate(auth: AuthenticationService) -> None:
    await _identified(auth)

    first = await auth.submit_verification(CALL, "1999-01-01")
    second = await auth.submit_verification(CALL, "1998-01-01")

    assert first.session.authentication_attempts == 1
    assert second.session.authentication_attempts == 2
    assert second.step is AuthenticationStep.VERIFICATION_FAILED
    assert second.attempts_remaining == 1


# --- maximum attempts ---------------------------------------------------------


async def test_the_third_failure_exhausts_the_budget(auth: AuthenticationService) -> None:
    await _identified(auth)

    for _ in range(MAX_AUTHENTICATION_ATTEMPTS - 1):
        await auth.submit_verification(CALL, "1999-01-01")
    final = await auth.submit_verification(CALL, "1999-01-01")

    assert final.step is AuthenticationStep.ATTEMPTS_EXHAUSTED
    assert final.session.authentication_status is AuthenticationStatus.AUTHENTICATION_FAILED
    assert final.session.conversation_outcome is ConversationOutcome.AUTHENTICATION_FAILED
    assert final.attempts_remaining == 0


async def test_the_correct_value_is_rejected_after_the_budget_is_spent(
    auth: AuthenticationService, customers: FakeCustomerRepository
) -> None:
    """A spent budget is terminal: guessing until you get there must not work."""
    await _identified(auth)
    for _ in range(MAX_AUTHENTICATION_ATTEMPTS):
        await auth.submit_verification(CALL, "1999-01-01")
    calls_before = customers.verify_calls

    result = await auth.submit_verification(CALL, MARIA_DOB)

    assert result.step is AuthenticationStep.ATTEMPTS_EXHAUSTED
    assert result.is_authenticated is False
    assert customers.verify_calls == calls_before  # not even checked


async def test_a_failed_session_cannot_restart_with_a_new_number(
    auth: AuthenticationService,
) -> None:
    """Re-entering the flow from the top must not reset the attempt budget."""
    await _identified(auth)
    for _ in range(MAX_AUTHENTICATION_ATTEMPTS):
        await auth.submit_verification(CALL, "1999-01-01")

    result = await auth.submit_phone(CALL, "+15550102345")

    assert result.step is AuthenticationStep.ATTEMPTS_EXHAUSTED
    assert result.session.authentication_status is AuthenticationStatus.AUTHENTICATION_FAILED
    assert result.session.customer_id == "CUST-1001"  # not switched to James


# --- customer not found -------------------------------------------------------


async def test_an_unknown_number_is_customer_not_found(auth: AuthenticationService) -> None:
    await auth.start_call(CALL)

    result = await auth.submit_phone(CALL, "+15550109999")

    assert result.step is AuthenticationStep.CUSTOMER_NOT_FOUND
    assert result.session.conversation_outcome is ConversationOutcome.CUSTOMER_NOT_FOUND


async def test_customer_not_found_is_not_authentication_failure(
    auth: AuthenticationService,
) -> None:
    """The distinction CLAUDE.md §10 requires, at the session level."""
    await auth.start_call(CALL)

    result = await auth.submit_phone(CALL, "+15550109999")

    assert result.session.authentication_status is AuthenticationStatus.UNAUTHENTICATED
    assert result.session.authentication_status is not AuthenticationStatus.AUTHENTICATION_FAILED
    assert result.session.authentication_attempts == 0
    assert result.attempts_remaining == MAX_AUTHENTICATION_ATTEMPTS


async def test_an_unknown_number_can_be_corrected(auth: AuthenticationService) -> None:
    """A mistyped number is not the end of the call."""
    await auth.start_call(CALL)
    await auth.submit_phone(CALL, "+15550109999")

    result = await auth.submit_phone(CALL, MARIA_PHONE)

    assert result.step is AuthenticationStep.VERIFICATION_REQUIRED


async def test_an_unparseable_number_is_reported_distinctly(
    auth: AuthenticationService,
) -> None:
    """'I didn't catch that' is a different sentence to 'you have no account'."""
    await auth.start_call(CALL)

    result = await auth.submit_phone(CALL, "banana")

    assert result.step is AuthenticationStep.PHONE_NOT_UNDERSTOOD
    assert result.session.conversation_outcome is None  # the call is not over


async def test_repeated_unknown_numbers_stop_rather_than_loop(
    auth: AuthenticationService,
) -> None:
    await auth.start_call(CALL)
    for _ in range(2):
        await auth.submit_phone(CALL, "+15550109999")

    final = await auth.submit_phone(CALL, "+15550109998")

    assert final.step is AuthenticationStep.LOOKUP_ATTEMPTS_EXHAUSTED
    assert final.session.authentication_attempts == 0  # still not a verification failure


async def test_verification_before_identification_asks_for_the_phone(
    auth: AuthenticationService, customers: FakeCustomerRepository
) -> None:
    await auth.start_call(CALL)

    result = await auth.submit_verification(CALL, MARIA_DOB)

    assert result.step is AuthenticationStep.PHONE_REQUIRED
    assert result.is_authenticated is False
    assert customers.verify_calls == 0


# --- integration failures -----------------------------------------------------


async def test_a_lookup_failure_is_not_reported_as_customer_not_found(
    auth: AuthenticationService, customers: FakeCustomerRepository
) -> None:
    customers.fail_lookup_with = FailureReason.UPSTREAM_TIMEOUT
    await auth.start_call(CALL)

    result = await auth.submit_phone(CALL, MARIA_PHONE)

    assert result.step is AuthenticationStep.INTEGRATION_ERROR
    assert result.step is not AuthenticationStep.CUSTOMER_NOT_FOUND
    assert result.session.conversation_outcome is None
    assert result.session.authentication_status is AuthenticationStatus.UNAUTHENTICATED


async def test_a_verification_failure_upstream_does_not_cost_an_attempt(
    auth: AuthenticationService, customers: FakeCustomerRepository
) -> None:
    """An unreachable sheet must not spend the caller's budget."""
    await _identified(auth)
    customers.fail_verify_with = FailureReason.UPSTREAM_ERROR

    result = await auth.submit_verification(CALL, MARIA_DOB)

    assert result.step is AuthenticationStep.INTEGRATION_ERROR
    assert result.session.authentication_attempts == 0
    assert result.attempts_remaining == MAX_AUTHENTICATION_ATTEMPTS
    assert result.session.authentication_status is AuthenticationStatus.CUSTOMER_FOUND


async def test_the_caller_can_retry_after_a_transient_upstream_failure(
    auth: AuthenticationService, customers: FakeCustomerRepository
) -> None:
    await _identified(auth)
    customers.fail_verify_with = FailureReason.UPSTREAM_TIMEOUT
    await auth.submit_verification(CALL, MARIA_DOB)

    customers.fail_verify_with = None
    result = await auth.submit_verification(CALL, MARIA_DOB)

    assert result.is_authenticated is True


async def test_a_record_that_vanishes_mid_call_is_not_a_failed_attempt(
    auth: AuthenticationService, customers: FakeCustomerRepository
) -> None:
    await _identified(auth)
    customers.customer_vanishes = True

    result = await auth.submit_verification(CALL, MARIA_DOB)

    assert result.step is AuthenticationStep.CUSTOMER_NOT_FOUND
    assert result.session.authentication_attempts == 0
    assert result.is_authenticated is False


# --- escalation and completion ------------------------------------------------


async def test_escalation_does_not_require_authentication(auth: AuthenticationService) -> None:
    """A caller who asks for a person gets one (CLAUDE.md §13)."""
    await auth.start_call(CALL)

    session = await auth.escalate(CALL, "caller asked for a representative")

    assert session.escalated is True
    assert session.is_authenticated is False


async def test_escalation_never_authenticates(auth: AuthenticationService) -> None:
    await _identified(auth)

    session = await auth.escalate(CALL, "caller asked for a representative")

    assert session.authentication_status is AuthenticationStatus.CUSTOMER_FOUND


async def test_completion_records_the_outcome(auth: AuthenticationService) -> None:
    await auth.start_call(CALL)

    session = await auth.complete(CALL, ConversationOutcome.RESOLVED)

    assert session.conversation_outcome is ConversationOutcome.RESOLVED


async def test_a_missing_session_is_recreated_unauthenticated(
    auth: AuthenticationService,
) -> None:
    """A lost session must fail closed, never open."""
    result = await auth.submit_verification("call-never-started", MARIA_DOB)

    assert result.is_authenticated is False
    assert result.step is AuthenticationStep.PHONE_REQUIRED
