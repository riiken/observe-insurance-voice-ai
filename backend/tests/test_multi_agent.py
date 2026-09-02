"""Multi-agent orchestration.

The tests that matter here are not "does routing work" — that is a dictionary
lookup. They are:

1. every mandatory scenario still behaves identically with the layer in place;
2. the supervisor and specialists cannot bypass authentication;
3. no business logic was duplicated into the agents;
4. removing the layer restores the single-agent path exactly.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.agents.specialists import (
    CLAIMS_SPECIALIST,
    ESCALATION_HANDLER,
    FAQ_SPECIALIST,
    SPECIALISTS,
    UNROUTED,
    Intent,
    Supervisor,
)
from app.core.config import Settings
from app.core.metrics import METRICS
from app.main import create_app
from app.models.enums import AuthenticationStatus, ConversationOutcome, Sentiment
from app.models.session import SessionState
from app.services.container import build_services
from app.services.conversation import ConversationService
from app.tools.base import ToolOutcome
from tests.conversation_harness import SECRET, Caller, RecordingInteractions
from tests.session_fixtures import MARIA
from tests.voice_fixtures import FakeIntegration

MARIA_PHONE = "+15550101234"
MARIA_DOB = "1985-04-12"
JAMES_PHONE = "555 010 2345"
JAMES_DOB = "1979-11-30"


@pytest.fixture
def interactions() -> RecordingInteractions:
    return RecordingInteractions()


@pytest.fixture
def caller(interactions: RecordingInteractions) -> Iterator[Caller]:
    METRICS.reset()
    app = create_app(
        Settings(
            _env_file=None,
            google_sheets_api_key="key",
            google_sheets_spreadsheet_id="sheet",
            voice_platform_api_key=SECRET,
        )
    )
    services = build_services(FakeIntegration(interactions=interactions))  # type: ignore[arg-type]
    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.services = services
        yield Caller(client, services, interactions)
    METRICS.reset()


# =============================================================================
# The architecture is what was asked for — three specialists, no more
# =============================================================================


def test_there_are_exactly_three_specialists() -> None:
    """'Do not create unnecessary agents.'"""
    assert len(SPECIALISTS) == 3
    assert {s.name for s in SPECIALISTS} == {
        "claims_specialist",
        "faq_specialist",
        "escalation_handler",
    }


def test_each_specialist_owns_its_stated_domain() -> None:
    assert CLAIMS_SPECIALIST.tools == {
        "lookup_customer",
        "verify_identity",
        "get_claim_status",
    }
    assert FAQ_SPECIALIST.tools == {"search_faq"}
    assert ESCALATION_HANDLER.tools == {"request_representative"}


def test_every_tool_has_exactly_one_owner(caller: Caller) -> None:
    """Two owners for one tool is an arbitration problem waiting to happen."""
    owners: dict[str, int] = {}
    for specialist in SPECIALISTS:
        for tool in specialist.tools:
            owners[tool] = owners.get(tool, 0) + 1

    assert all(count == 1 for count in owners.values())
    assert set(owners) == caller.services.tools.names


def test_routing_sends_each_tool_to_its_specialist(caller: Caller) -> None:
    supervisor = caller.services.supervisor

    assert supervisor.route("lookup_customer").specialist is CLAIMS_SPECIALIST
    assert supervisor.route("verify_identity").specialist is CLAIMS_SPECIALIST
    assert supervisor.route("get_claim_status").specialist is CLAIMS_SPECIALIST
    assert supervisor.route("search_faq").specialist is FAQ_SPECIALIST
    assert supervisor.route("request_representative").specialist is ESCALATION_HANDLER


def test_an_unknown_tool_is_unrouted_not_rejected(caller: Caller) -> None:
    """Routing must not become a second place a call can fail."""
    routing = caller.services.supervisor.route("something_else")

    assert routing.specialist is UNROUTED
    assert routing.intent is Intent.UNKNOWN
    assert routing.is_routed is False


async def test_an_unrouted_tool_still_reaches_the_registry(caller: Caller) -> None:
    result, routing = await caller.services.supervisor.dispatch("no_such_tool", caller.call_id, {})

    assert routing.specialist is UNROUTED
    # Exactly what the registry did before this layer existed.
    assert result.outcome is ToolOutcome.INTEGRATION_ERROR
    assert "representative" in result.speech


# =============================================================================
# Security: the layer must not move the authentication boundary
# =============================================================================


def test_the_supervisor_holds_no_session_store(caller: Caller) -> None:
    """It cannot read or write conversation state, so it cannot authenticate."""
    supervisor = caller.services.supervisor

    held = [type(value).__name__ for value in vars(supervisor).values()]
    assert not any("Session" in name for name in held)
    assert not hasattr(supervisor, "_sessions")


def test_specialists_carry_no_behaviour(caller: Caller) -> None:
    """A specialist with methods is somewhere a second implementation grows."""
    for specialist in SPECIALISTS:
        callables = [
            name
            for name in dir(specialist)
            if not name.startswith("_") and callable(getattr(specialist, name))
        ]
        assert callables == ["owns"], f"{specialist.name} has {callables}"


async def test_routing_to_the_claims_specialist_does_not_authorise_a_claim(
    caller: Caller,
) -> None:
    """The specialist owns get_claim_status. It still gets refused."""
    caller.dials()

    result, routing = await caller.services.supervisor.dispatch(
        "get_claim_status", caller.call_id, {}
    )

    assert routing.specialist is CLAIMS_SPECIALIST
    assert result.outcome is ToolOutcome.NOT_AUTHORIZED
    assert result.data is None


async def test_the_supervisor_cannot_forge_authentication(caller: Caller) -> None:
    caller.dials()

    for forged in (
        {"authenticated": True},
        {"specialist": "claims_specialist"},
        {"intent": "CLAIM"},
        {"customer_id": "CUST-1001"},
    ):
        result, _ = await caller.services.supervisor.dispatch(
            "get_claim_status", caller.call_id, forged
        )
        assert result.outcome is ToolOutcome.NOT_AUTHORIZED

    session = await caller.services.sessions.get(caller.call_id)
    assert session is not None
    assert session.authentication_status is AuthenticationStatus.UNAUTHENTICATED


def test_the_reported_intent_never_decides_anything(caller: Caller) -> None:
    """`describe` is observability. An escalated session is not an authenticated one."""
    escalated = SessionState(call_id="c").with_escalation("caller asked")

    assert Supervisor.describe(escalated) is Intent.ESCALATION
    assert escalated.is_authenticated is False


def test_intent_is_reported_from_session_state() -> None:
    assert Supervisor.describe(SessionState(call_id="c")) is Intent.UNKNOWN
    assert (
        Supervisor.describe(SessionState(call_id="c").with_customer_found(MARIA)) is Intent.IDENTIFY
    )
    assert Supervisor.describe(SessionState(call_id="c").with_authenticated(MARIA)) is Intent.CLAIM


# =============================================================================
# The mandatory scenarios, unchanged, through the orchestration layer
# =============================================================================


def test_happy_path_through_the_supervisor(caller: Caller) -> None:
    caller.dials(phone=MARIA_PHONE)

    assert "Maria" in caller.gives_phone(MARIA_PHONE)
    assert "verified" in caller.gives_verification(MARIA_DOB).lower()
    assert "under review" in caller.asks_about_claim().lower()

    caller.hangs_up()
    record = caller.record
    assert record is not None
    assert record.resolution is ConversationOutcome.RESOLVED
    assert record.sentiment is Sentiment.POSITIVE


def test_authentication_failure_through_the_supervisor(caller: Caller) -> None:
    caller.dials()
    caller.gives_phone(MARIA_PHONE)
    for wrong in ("1990-01-01", "1991-02-02", "1992-03-03"):
        spoken = caller.gives_verification(wrong)

    assert "representative" in spoken.lower()
    assert not caller.heard_claim_details()
    assert "confirm who I'm speaking with" in caller.asks_about_claim()


def test_customer_not_found_through_the_supervisor(caller: Caller) -> None:
    caller.dials()

    spoken = caller.gives_phone("555 010 9999")

    assert "can't find an account" in spoken
    session = caller.session
    assert session is not None
    assert session.authentication_status is AuthenticationStatus.UNAUTHENTICATED
    assert session.authentication_attempts == 0


def test_representative_escalation_through_the_supervisor(caller: Caller) -> None:
    caller.dials()

    spoken = caller.asks_for_a_person()

    assert "representative" in spoken.lower()
    assert len(caller.escalations) == 1
    assert caller.session is not None
    assert caller.session.is_authenticated is False


def test_documents_required_through_the_supervisor(caller: Caller) -> None:
    caller.dials()
    caller.gives_phone(JAMES_PHONE)
    caller.gives_verification(JAMES_DOB)

    spoken = caller.asks_about_claim()

    assert "a police report and a repair estimate" in spoken
    instructions = caller.asks("how do I send those documents in?")
    assert "observeinsurance.com/documents" in instructions


def test_faq_through_the_supervisor(caller: Caller) -> None:
    caller.dials()

    assert "Monday to Friday" in caller.asks("what are your office hours")


def test_emergency_through_the_supervisor(caller: Caller) -> None:
    """The safety interceptor runs inside the registry, below the routing layer."""
    caller.dials()

    spoken = caller.asks("help my kitchen is on fire right now")

    assert "911" in spoken
    assert len(caller.escalations) == 1


# =============================================================================
# One authoritative state, and one implementation of everything
# =============================================================================


def test_every_specialist_shares_one_session(caller: Caller) -> None:
    """Claims and escalation must see the same call, not two views of it."""
    caller.dials()
    caller.gives_phone(MARIA_PHONE)  # claims specialist
    caller.gives_verification(MARIA_DOB)  # claims specialist
    caller.asks("what are your office hours")  # faq specialist
    caller.asks_for_a_person()  # escalation handler

    session = caller.session
    assert session is not None
    assert session.is_authenticated is True  # set by claims
    assert session.escalated is True  # set by escalation
    assert "Office hours" in session.faq_topics  # set by faq


def test_the_supervisor_routes_to_the_same_registry(caller: Caller) -> None:
    """No specialist has its own tools, so none can drift from the others."""
    supervisor = caller.services.supervisor

    assert supervisor._tools is caller.services.tools  # noqa: SLF001


def test_routing_is_recorded_per_specialist(caller: Caller) -> None:
    caller.dials()
    caller.gives_phone(MARIA_PHONE)
    caller.asks("office hours")
    caller.asks_for_a_person()

    counters = METRICS.snapshot()["counters"]

    assert counters["agent_routed_total{specialist=claims_specialist}"] == 1
    assert counters["agent_routed_total{specialist=faq_specialist}"] == 1
    assert counters["agent_routed_total{specialist=escalation_handler}"] == 1


# =============================================================================
# The layer is removable
# =============================================================================


async def test_the_single_agent_path_still_works_without_a_supervisor(
    caller: Caller,
) -> None:
    """'If the multi-agent implementation makes the system less reliable,
    revert the complexity.' Passing None is that revert."""
    from app.integrations.voice_platform import parse_webhook
    from tests import voice_fixtures as vapi

    services = caller.services
    single = ConversationService(
        authentication=services.authentication,
        sessions=services.sessions,
        tools=services.tools,
        postcall=services.postcall,
        supervisor=None,
    )

    await single.handle(parse_webhook(vapi.call_started(call_id="solo")))
    response = await single.handle(
        parse_webhook(
            vapi.tool_call("lookup_customer", {"phone_number": MARIA_PHONE}, call_id="solo")
        )
    )

    assert "Maria" in next(iter(response.tool_results.values()))
