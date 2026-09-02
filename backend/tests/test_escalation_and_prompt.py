"""Escalation records and the system prompt."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.prompt import PromptConfigurationError, load_system_prompt
from app.models.enums import EscalationReason, EscalationStatus
from app.services.escalation import EscalationService
from app.services.safety import SafetyService
from app.services.session_store import InMemorySessionStore
from app.tools.base import ToolOutcome
from app.tools.representative_tool import RequestRepresentativeTool
from tests.session_fixtures import MARIA

CALL = "call-1"


# --- escalation ---------------------------------------------------------------


async def test_an_escalation_record_has_everything_a_router_needs() -> None:
    store = InMemorySessionStore()

    record = await EscalationService(store).request_representative(CALL)

    assert record.escalation_id.startswith("ESC-")
    assert record.call_id == CALL
    assert record.reason is EscalationReason.CALLER_REQUEST
    assert record.status is EscalationStatus.REQUESTED
    assert record.created_at.tzinfo is not None


async def test_escalation_works_without_authentication() -> None:
    """A caller who asks for a person gets one (CLAUDE.md §13)."""
    store = InMemorySessionStore()

    record = await EscalationService(store).request_representative(CALL)

    assert record.authenticated is False
    assert record.customer_id is None

    session = await store.get(CALL)
    assert session is not None
    assert session.escalated is True


async def test_an_escalation_from_a_verified_call_carries_the_customer() -> None:
    from app.models.session import SessionState

    store = InMemorySessionStore()
    await store.save(SessionState(call_id=CALL).with_authenticated(MARIA))

    record = await EscalationService(store).request_representative(CALL)

    assert record.customer_id == "CUST-1001"
    assert record.authenticated is True


async def test_an_emergency_is_flagged_and_answered_safely() -> None:
    tool = RequestRepresentativeTool(EscalationService(InMemorySessionStore()), SafetyService())

    result = await tool(CALL, reason="EMERGENCY")

    assert result.outcome is ToolOutcome.SUCCESS
    assert "911" in result.speech
    # Must not pretend to be an emergency service, or carry on about the claim.
    assert "claim status" not in result.speech.lower()
    assert result.context["reason"] == "EMERGENCY"


async def test_an_unrecognised_reason_still_escalates() -> None:
    """Refusing to transfer because of a bad enum would be the wrong failure."""
    tool = RequestRepresentativeTool(EscalationService(InMemorySessionStore()), SafetyService())

    result = await tool(CALL, reason="because I said so")

    assert result.outcome is ToolOutcome.SUCCESS
    assert result.context["reason"] == "CALLER_REQUEST"


async def test_records_are_retrievable_per_call() -> None:
    service = EscalationService(InMemorySessionStore())
    await service.request_representative("call-a")
    await service.request_representative("call-b")

    assert len(service.records_for("call-a")) == 1
    assert service.count == 2


# --- the system prompt --------------------------------------------------------


def test_the_prompt_loads() -> None:
    assert "Observe Insurance" in load_system_prompt()


@pytest.mark.parametrize(
    "requirement",
    [
        "Thanks for calling Observe Insurance",  # 1. greet
        "phone number on their account",  # 2. ask for the phone number
        "lookup_customer",  # 3. customer lookup
        "verify_identity",  # 4. verify identity
        "get_claim_status",  # 5. only then discuss claims
        "search_faq",  # 6. answer supported questions
        "request_representative",  # 7. escalate
        "Do not improvise.",  # 8. unsupported questions
        "911",  # 9. emergencies
        "Calm, warm and brief",  # 10. tone
    ],
)
def test_the_prompt_covers_every_required_behaviour(requirement: str) -> None:
    assert requirement in load_system_prompt()


@pytest.mark.parametrize(
    "rule",
    [
        "one** question at a time",
        "Never promise",
        "Never read out JSON",
        "The backend decides who is verified",
        "You have no way to mark anyone as verified",
    ],
)
def test_the_prompt_states_the_voice_and_security_rules(rule: str) -> None:
    assert rule in load_system_prompt()


def test_an_empty_prompt_file_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "prompt.md"
    path.write_text("   ", encoding="utf-8")

    with pytest.raises(PromptConfigurationError):
        load_system_prompt(path)


def test_a_missing_prompt_file_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(PromptConfigurationError):
        load_system_prompt(tmp_path / "nope.md")
