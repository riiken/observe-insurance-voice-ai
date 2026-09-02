"""FAQ answering, escalation records, and the system prompt."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.prompt import PromptConfigurationError, load_system_prompt
from app.models.enums import EscalationReason, EscalationStatus
from app.services.escalation import EscalationService
from app.services.faq import (
    DEFAULT_FAQ_PATH,
    FaqConfigurationError,
    FaqOutcome,
    FaqService,
    load_faq_content,
)
from app.services.session_store import InMemorySessionStore
from app.tools.base import ToolOutcome
from app.tools.faq_tool import SearchFaqTool
from app.tools.representative_tool import RequestRepresentativeTool
from tests.session_fixtures import MARIA

CALL = "call-1"


@pytest.fixture
def faq() -> FaqService:
    return FaqService(load_faq_content())


# --- FAQ: the four supported topics -------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What are your office hours?", "office_hours"),
        ("When are you open?", "office_hours"),
        ("Are you open on Saturday?", "office_hours"),
        ("What's your mailing address?", "mailing_address"),
        ("Where do I post things?", "mailing_address"),
        ("How do I start a new claim?", "start_a_claim"),
        ("I want to file a claim", "start_a_claim"),
        ("How does the claims process work?", "claims_process"),
        ("What happens after I submit?", "claims_process"),
        ("How do I upload documents?", "document_submission"),
    ],
)
def test_supported_questions_are_answered(faq: FaqService, question: str, expected: str) -> None:
    result = faq.search(question)

    assert result.is_answered
    assert result.entry is not None
    assert result.entry.id == expected


@pytest.mark.parametrize(
    "question",
    [
        "What's the weather in Boston?",
        "Can you sell me a car?",
        "Am I covered for flood damage?",
        "What's my premium?",
        "Who is the CEO?",
        "",
        "   ",
    ],
)
def test_unsupported_questions_are_not_answered(faq: FaqService, question: str) -> None:
    """The agent should not hallucinate unsupported FAQ answers (CLAUDE.md §12)."""
    assert faq.search(question).outcome is FaqOutcome.NO_ANSWER


async def test_an_unanswerable_question_offers_a_representative(faq: FaqService) -> None:
    result = await SearchFaqTool(faq)(CALL, "Am I covered for flood damage?")

    assert result.outcome is ToolOutcome.NOT_FOUND
    assert "representative" in result.speech
    # It says what it *can* help with rather than just refusing.
    assert "office hours" in result.speech.lower()


async def test_an_answered_question_is_read_out_verbatim(faq: FaqService) -> None:
    expected = faq.search("office hours").entry
    assert expected is not None

    result = await SearchFaqTool(faq)(CALL, "what are your office hours")

    assert result.speech == expected.answer


def test_faq_answers_contain_no_markup(faq: FaqService) -> None:
    """Every answer is spoken aloud."""
    for topic in faq.topics:
        assert topic

    content = load_faq_content()
    for entry in content.entries:
        for artefact in ("{", "}", "[", "]", "*", "#", "|", "\n-"):
            assert artefact not in entry.answer


def test_duplicate_faq_ids_are_rejected(tmp_path: Path) -> None:
    payload = json.loads(DEFAULT_FAQ_PATH.read_text(encoding="utf-8"))
    payload.pop("_comment", None)
    payload["entries"].append(payload["entries"][0])
    path = tmp_path / "faq.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FaqConfigurationError):
        load_faq_content(path)


def test_a_missing_faq_file_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(FaqConfigurationError):
        load_faq_content(tmp_path / "nope.json")


# --- escalation ---------------------------------------------------------------


async def test_an_escalation_record_has_everything_a_router_needs() -> None:
    store = InMemorySessionStore()

    record = await EscalationService(store).request_representative(CALL)

    assert record.escalation_id.startswith("ESC-")
    assert record.call_id == CALL
    assert record.reason is EscalationReason.CALLER_REQUEST
    assert record.status is EscalationStatus.PENDING
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
    tool = RequestRepresentativeTool(EscalationService(InMemorySessionStore()))

    result = await tool(CALL, reason="EMERGENCY")

    assert result.outcome is ToolOutcome.SUCCESS
    assert "911" in result.speech
    # Must not pretend to be an emergency service, or carry on about the claim.
    assert "claim status" not in result.speech.lower()
    assert result.context["reason"] == "EMERGENCY"


async def test_an_unrecognised_reason_still_escalates() -> None:
    """Refusing to transfer because of a bad enum would be the wrong failure."""
    tool = RequestRepresentativeTool(EscalationService(InMemorySessionStore()))

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
