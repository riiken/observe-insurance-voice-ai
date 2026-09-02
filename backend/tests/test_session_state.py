"""SessionState: the authorization source of truth."""

from __future__ import annotations

import dataclasses

import pytest

from app.models.enums import AuthenticationStatus, ConversationOutcome
from app.models.session import MAX_AUTHENTICATION_ATTEMPTS, SessionState
from tests.session_fixtures import MARIA


def _session() -> SessionState:
    return SessionState(call_id="call-1")


# --- the flag cannot simply be set -------------------------------------------


def test_authentication_status_cannot_be_assigned() -> None:
    """'authenticated = True' is not an available move, by construction."""
    session = _session()

    with pytest.raises(dataclasses.FrozenInstanceError):
        session.authentication_status = AuthenticationStatus.AUTHENTICATED  # type: ignore[misc]


@pytest.mark.parametrize("field", ["customer_id", "authentication_attempts", "claim_id"])
def test_no_field_can_be_assigned(field: str) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(_session(), field, "anything")


def test_a_new_session_starts_unauthenticated() -> None:
    session = _session()

    assert session.authentication_status is AuthenticationStatus.UNAUTHENTICATED
    assert session.is_authenticated is False
    assert session.authentication_attempts == 0
    assert session.customer_id is None
    assert session.claim_id is None
    assert session.escalated is False
    assert session.conversation_outcome is None


# --- transitions --------------------------------------------------------------


def test_customer_found_identifies_but_does_not_authenticate() -> None:
    """Claiming an identity is not proving one."""
    session = _session().with_customer_found(MARIA)

    assert session.authentication_status is AuthenticationStatus.CUSTOMER_FOUND
    assert session.is_authenticated is False
    assert session.customer_id == "CUST-1001"
    assert session.customer_name == "Maria Alvarez"


def test_customer_not_found_is_distinct_from_authentication_failure() -> None:
    """Nothing was checked, so nothing was failed."""
    session = _session().with_customer_not_found()

    assert session.authentication_status is AuthenticationStatus.UNAUTHENTICATED
    assert session.authentication_status is not AuthenticationStatus.AUTHENTICATION_FAILED
    assert session.authentication_attempts == 0
    assert session.conversation_outcome is ConversationOutcome.CUSTOMER_NOT_FOUND


def test_a_wrong_answer_costs_an_attempt_and_allows_a_retry() -> None:
    session = _session().with_customer_found(MARIA).with_verification_failed()

    assert session.authentication_status is AuthenticationStatus.CUSTOMER_FOUND
    assert session.authentication_attempts == 1
    assert session.attempts_remaining == MAX_AUTHENTICATION_ATTEMPTS - 1
    assert session.can_attempt_verification is True


def test_the_third_wrong_answer_is_terminal() -> None:
    session = _session().with_customer_found(MARIA)
    for _ in range(MAX_AUTHENTICATION_ATTEMPTS):
        session = session.with_verification_failed()

    assert session.authentication_status is AuthenticationStatus.AUTHENTICATION_FAILED
    assert session.authentication_attempts == MAX_AUTHENTICATION_ATTEMPTS
    assert session.attempts_remaining == 0
    assert session.can_attempt_verification is False
    assert session.is_terminal is True
    assert session.conversation_outcome is ConversationOutcome.AUTHENTICATION_FAILED


def test_authentication_sets_the_only_state_that_authorises_claims() -> None:
    session = _session().with_customer_found(MARIA).with_authenticated(MARIA)

    assert session.authentication_status is AuthenticationStatus.AUTHENTICATED
    assert session.is_authenticated is True
    assert session.is_terminal is True


def test_verification_started_is_not_authenticated() -> None:
    """An in-flight check must not read as a completed one."""
    session = _session().with_customer_found(MARIA).with_verification_started()

    assert session.authentication_status is AuthenticationStatus.VERIFYING
    assert session.is_authenticated is False


def test_abandoning_verification_costs_nothing() -> None:
    """An upstream failure is our problem, not the caller's budget."""
    session = _session().with_customer_found(MARIA).with_verification_started()

    resumed = session.with_verification_abandoned()

    assert resumed.authentication_status is AuthenticationStatus.CUSTOMER_FOUND
    assert resumed.authentication_attempts == 0


def test_verification_cannot_be_attempted_before_a_customer_is_identified() -> None:
    assert _session().can_attempt_verification is False


def test_escalation_is_available_in_any_state() -> None:
    session = _session().with_escalation("caller asked for a person")

    assert session.escalated is True
    assert session.escalation_reason == "caller asked for a person"
    assert session.conversation_outcome is ConversationOutcome.ESCALATED
    assert session.is_authenticated is False


def test_transitions_return_new_objects_and_bump_the_timestamp() -> None:
    original = _session()

    updated = original.with_customer_found(MARIA)

    assert updated is not original
    assert original.customer_id is None  # the original is untouched
    assert updated.updated_at >= original.updated_at


def test_log_fields_carry_no_secret_and_no_claim_detail() -> None:
    session = _session().with_authenticated(MARIA).with_claim("CLM-88401")

    fields = session.log_fields()

    assert "caller_phone" not in fields
    assert "verification_value" not in fields
    assert "claim_id" not in fields
    assert fields["authentication_status"] is AuthenticationStatus.AUTHENTICATED
