"""The call lifecycle, driven by platform events.

These run the real tool registry and real services against fake repositories,
so a full call can be walked end to end without a voice platform.
"""

from __future__ import annotations

import pytest

from app.integrations.voice_platform import VoiceEventType, parse_webhook
from app.models.enums import AuthenticationStatus, ConversationOutcome
from app.models.session import SessionState
from app.services.container import ServiceContainer
from app.services.conversation import _derive_outcome
from app.tools.base import ToolOutcome, ToolResult
from app.tools.registry import ToolDefinition, ToolRegistry
from tests import voice_fixtures as vapi
from tests.session_fixtures import MARIA
from tests.voice_fixtures import CALL_ID, JAMES_DOB, JAMES_PHONE, MARIA_DOB, MARIA_PHONE


@pytest.fixture
def services() -> ServiceContainer:
    return vapi.build_container()


async def _handle(services: ServiceContainer, payload: dict) -> dict[str, str]:
    response = await services.conversation.handle(parse_webhook(payload))
    return response.tool_results


async def _speak(services: ServiceContainer, tool: str, **arguments: object) -> str:
    results = await _handle(services, vapi.tool_call(tool, arguments))
    return next(iter(results.values()), "")


# --- lifecycle ----------------------------------------------------------------


async def test_a_call_start_creates_a_session(services: ServiceContainer) -> None:
    await _handle(services, vapi.call_started(phone=MARIA_PHONE))

    session = await services.sessions.get(CALL_ID)
    assert session is not None
    assert session.caller_phone == MARIA_PHONE
    assert session.authentication_status is AuthenticationStatus.UNAUTHENTICATED


async def test_the_platform_caller_id_does_not_authenticate(
    services: ServiceContainer,
) -> None:
    """Knowing the number a call came from proves nothing about who is holding it."""
    await _handle(services, vapi.call_started(phone=MARIA_PHONE))

    session = await services.sessions.get(CALL_ID)
    assert session is not None
    assert session.is_authenticated is False


async def test_an_event_without_a_call_id_is_ignored(services: ServiceContainer) -> None:
    payload = {"message": {"type": "tool-calls", "toolCallList": []}}

    response = await services.conversation.handle(parse_webhook(payload))

    assert response.event_type is VoiceEventType.IGNORED


async def test_a_tool_call_arriving_before_the_start_event_still_works(
    services: ServiceContainer,
) -> None:
    """Webhooks can arrive out of order; the session is created on demand."""
    speech = await _speak(services, "lookup_customer", phone_number=MARIA_PHONE)

    assert "date of birth" in speech.lower()


# --- call completion ----------------------------------------------------------


async def test_completion_records_the_outcome_and_releases_the_session(
    services: ServiceContainer,
) -> None:
    await _handle(services, vapi.call_started())
    await _speak(services, "lookup_customer", phone_number=MARIA_PHONE)
    await _speak(services, "verify_identity", verification_value=MARIA_DOB)

    await _handle(services, vapi.end_of_call())

    assert await services.sessions.get(CALL_ID) is None
    assert await services.sessions.count() == 0


@pytest.mark.parametrize(
    ("session", "expected"),
    [
        (SessionState(call_id=CALL_ID), ConversationOutcome.ABANDONED),
        (SessionState(call_id=CALL_ID).with_customer_found(MARIA), ConversationOutcome.ABANDONED),
        (
            SessionState(call_id=CALL_ID).with_authenticated(MARIA),
            ConversationOutcome.RESOLVED,
        ),
        # Escalation wins: a call handed to a human is escalated, whatever else
        # happened on the way there.
        (
            SessionState(call_id=CALL_ID).with_authenticated(MARIA).with_escalation("x"),
            ConversationOutcome.ESCALATED,
        ),
        (
            SessionState(call_id=CALL_ID).with_escalation("x"),
            ConversationOutcome.ESCALATED,
        ),
        # An outcome recorded during the call survives completion.
        (
            SessionState(call_id=CALL_ID).with_customer_not_found(),
            ConversationOutcome.CUSTOMER_NOT_FOUND,
        ),
    ],
)
def test_the_outcome_is_derived_from_state_not_from_the_transcript(
    session: SessionState, expected: ConversationOutcome
) -> None:
    """What we observed, not how the model chose to summarise it."""
    assert _derive_outcome(session) is expected


def test_a_failed_verification_ends_as_authentication_failed() -> None:
    session = SessionState(call_id=CALL_ID).with_customer_found(MARIA)
    for _ in range(3):
        session = session.with_verification_failed()

    assert _derive_outcome(session) is ConversationOutcome.AUTHENTICATION_FAILED


async def test_a_verified_call_ends_as_resolved(services: ServiceContainer) -> None:
    await _handle(services, vapi.call_started())
    await _speak(services, "lookup_customer", phone_number=MARIA_PHONE)
    await _speak(services, "verify_identity", verification_value=MARIA_DOB)

    session = await services.sessions.get(CALL_ID)
    assert session is not None
    assert _derive_outcome(session) is ConversationOutcome.RESOLVED


async def test_an_escalated_call_ends_as_escalated(services: ServiceContainer) -> None:
    await _handle(services, vapi.call_started())
    await _speak(services, "request_representative")

    session = await services.sessions.get(CALL_ID)
    assert session is not None
    assert _derive_outcome(session) is ConversationOutcome.ESCALATED


async def test_completion_of_an_unknown_call_is_harmless(
    services: ServiceContainer,
) -> None:
    await _handle(services, vapi.end_of_call(call_id="never-seen"))


# --- the full happy path ------------------------------------------------------


async def test_a_whole_call_from_greeting_to_claim(services: ServiceContainer) -> None:
    await _handle(services, vapi.call_started(phone=MARIA_PHONE))

    lookup = await _speak(services, "lookup_customer", phone_number="555-010-1234")
    assert "Maria" in lookup

    verify = await _speak(services, "verify_identity", verification_value=MARIA_DOB)
    assert "verified" in verify.lower()

    claim = await _speak(services, "get_claim_status")
    assert "under review" in claim.lower()


async def test_the_documents_required_path(services: ServiceContainer) -> None:
    await _handle(services, vapi.call_started())
    await _speak(services, "lookup_customer", phone_number=JAMES_PHONE)
    await _speak(services, "verify_identity", verification_value=JAMES_DOB)

    claim = await _speak(services, "get_claim_status")

    assert "police report" in claim.lower()
    assert "repair estimate" in claim.lower()


# --- tool dispatch ------------------------------------------------------------


async def test_every_exposed_tool_is_reachable(services: ServiceContainer) -> None:
    assert services.tools.names == {
        "lookup_customer",
        "verify_identity",
        "get_claim_status",
        "search_faq",
        "request_representative",
    }


async def test_no_raw_data_access_tool_is_exposed(services: ServiceContainer) -> None:
    """The whole attack surface is five narrow operations."""
    for forbidden in ("execute_query", "run_code", "api_call", "sql", "get_customer_row"):
        assert forbidden not in services.tools.names


async def test_an_unknown_tool_fails_safely(services: ServiceContainer) -> None:
    result = await services.tools.invoke("drop_everything", CALL_ID, {})

    assert result.outcome is ToolOutcome.INTEGRATION_ERROR
    assert "representative" in result.speech


async def test_a_tool_that_raises_does_not_take_the_call_down() -> None:
    """A bug in a tool must cost a sentence, not a phone call."""

    async def _explode(**_: object) -> ToolResult:
        raise RuntimeError("boom")

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="exploding_tool",
                description="Always fails.",
                parameters=(),
                handler=_explode,
            )
        ]
    )

    result = await registry.invoke("exploding_tool", CALL_ID, {})

    assert result.outcome is ToolOutcome.INTEGRATION_ERROR
    assert "representative" in result.speech


async def test_missing_required_arguments_fail_safely(services: ServiceContainer) -> None:
    result = await services.tools.invoke("search_faq", CALL_ID, {})

    assert result.outcome is ToolOutcome.INTEGRATION_ERROR


async def test_arguments_the_model_invented_are_dropped(
    services: ServiceContainer,
) -> None:
    """A stray `authenticated=true` must never reach a handler."""
    result = await services.tools.invoke(
        "search_faq",
        CALL_ID,
        {"question": "office hours", "authenticated": True, "customer_id": "CUST-9"},
    )

    assert result.outcome is ToolOutcome.SUCCESS
    assert "Monday" in result.speech


async def test_call_id_is_not_a_tool_parameter(services: ServiceContainer) -> None:
    """The model cannot name a different call and inherit its authentication."""
    for definition in services.tools.definitions:
        assert "call_id" not in {parameter.name for parameter in definition.parameters}
