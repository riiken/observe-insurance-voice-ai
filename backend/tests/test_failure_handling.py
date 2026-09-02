"""Every external boundary, deliberately broken.

One section per failure mode in the brief. Each asserts the same four things:

1. the failure is **classified** correctly — and in particular that an
   infrastructure failure never becomes a business outcome;
2. it is **logged** at a level someone will or will not see, as appropriate;
3. the caller hears something **safe** and useful;
4. nothing **leaks** — no status codes, spreadsheet ids, stack traces or
   internal vocabulary reaches a caller.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import IntegrationError
from app.core.failures import (
    FAILURE_CATALOGUE,
    FailureClass,
    Severity,
    classify,
    is_infrastructure_failure,
)
from app.core.retry import retry_async
from app.integrations.repositories import FailureReason, PersistResult
from app.integrations.sheets.auth import ApiKeyAuthorizer
from app.integrations.sheets.client import GoogleSheetsClient
from app.integrations.voice_platform import SECRET_HEADER
from app.main import create_app
from app.models.enums import ClaimLookupOutcome, CustomerLookupOutcome
from app.services.container import build_services
from app.tools.base import ToolOutcome
from tests.conversation_harness import SECRET, Caller, RecordingInteractions
from tests.sheets_fixtures import (
    CLAIM_HEADER,
    CUSTOMER_HEADER,
    claims_repository,
    customer_repository,
)
from tests.voice_fixtures import FakeIntegration

WEBHOOK = "/api/v1/voice/webhook"
MARIA_PHONE = "+15550101234"
MARIA_DOB = "1985-04-12"

# Anything a caller must never hear, whatever goes wrong.
LEAKS = (
    "traceback",
    "exception",
    "500",
    "503",
    "403",
    "google",
    "sheet",
    "spreadsheet",
    "httpx",
    "none",
    "null",
    "{",
    "}",
)


def _assert_safe(spoken: str) -> None:
    lowered = spoken.lower()
    for leak in LEAKS:
        assert leak not in lowered, f"leaked {leak!r} in: {spoken}"
    assert spoken and spoken[0].isupper()


@pytest.fixture
def interactions() -> RecordingInteractions:
    return RecordingInteractions()


@pytest.fixture
def caller(interactions: RecordingInteractions) -> Iterator[Caller]:
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


# =============================================================================
# 1. Google Sheets timeout
# =============================================================================


async def test_1_timeout_is_classified_as_transient_not_as_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    result = await customer_repository(handler=handler).lookup_customer_by_phone(MARIA_PHONE)

    assert result.outcome is CustomerLookupOutcome.INTEGRATION_ERROR
    assert result.outcome is not CustomerLookupOutcome.CUSTOMER_NOT_FOUND
    assert result.reason is FailureReason.UPSTREAM_TIMEOUT
    assert classify("INTEGRATION_TIMEOUT").failure_class is FailureClass.TRANSIENT_UPSTREAM


async def test_1_timeout_is_retried() -> None:
    assert classify("INTEGRATION_TIMEOUT").retried is True


async def test_1_a_retry_schedule_cannot_outlast_a_caller() -> None:
    """Three attempts at ten seconds is thirty seconds of silence."""
    attempts = 0
    clock = [0.0]

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        clock[0] += 5.0  # each attempt burns five seconds
        raise IntegrationError(integration="g", retryable=True)

    async def sleep(delay: float) -> None:
        clock[0] += delay

    with pytest.raises(IntegrationError):
        await retry_async(
            operation,
            max_retries=5,
            backoff_base_seconds=0.1,
            is_transient=lambda _e: True,
            operation_name="test",
            total_budget_seconds=6.0,
            sleep=sleep,
            monotonic=lambda: clock[0],
        )

    # Attempts alone would have allowed six; the clock stopped it at two.
    assert attempts == 2


async def test_1_the_budget_does_not_apply_where_nobody_is_waiting() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise IntegrationError(integration="g", retryable=True)

    with pytest.raises(IntegrationError):
        await retry_async(
            operation,
            max_retries=3,
            backoff_base_seconds=0.0,
            is_transient=lambda _e: True,
            operation_name="test",
            total_budget_seconds=None,
            sleep=lambda _d: _noop(),
        )

    assert attempts == 4


async def _noop() -> None:
    return None


def test_1_a_timeout_tells_the_caller_something_safe(caller: Caller) -> None:
    caller.services.authentication._customers.fail_lookup_with = (  # noqa: SLF001
        FailureReason.UPSTREAM_TIMEOUT
    )
    caller.dials()

    spoken = caller.gives_phone(MARIA_PHONE)

    assert "trouble reaching our records" in spoken
    assert "can't find an account" not in spoken  # never a business outcome
    _assert_safe(spoken)


# =============================================================================
# 2. Google Sheets unavailable
# =============================================================================


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
async def test_2_server_errors_are_transient(status_code: int) -> None:
    repository = customer_repository(handler=lambda _r: httpx.Response(status_code))

    result = await repository.lookup_customer_by_phone(MARIA_PHONE)

    assert result.outcome is CustomerLookupOutcome.INTEGRATION_ERROR
    assert result.reason is FailureReason.UPSTREAM_ERROR


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
async def test_2_client_errors_are_permanent_and_not_retried(status_code: int) -> None:
    """A bad key or an unshared sheet will keep saying no."""
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code)

    client = GoogleSheetsClient(
        spreadsheet_id="s",
        authorizer=ApiKeyAuthorizer("k"),
        max_retries=3,
        backoff_base_seconds=0.0,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(IntegrationError) as caught:
        await client.get_values("Customers!A:D")

    assert attempts == 1
    assert caught.value.code == "UPSTREAM_PERMANENT"
    assert classify("UPSTREAM_PERMANENT").failure_class is FailureClass.PERMANENT_UPSTREAM
    await client.aclose()


async def test_2_our_http_status_is_not_the_upstreams() -> None:
    """A Sheets 403 must not become our 403."""
    client = GoogleSheetsClient(
        spreadsheet_id="s",
        authorizer=ApiKeyAuthorizer("k"),
        max_retries=0,
        transport=httpx.MockTransport(lambda _r: httpx.Response(403)),
    )

    with pytest.raises(IntegrationError) as caught:
        await client.get_values("Customers!A:D")

    assert caught.value.status_code == 502
    assert caught.value.context["upstream_status"] == 403
    await client.aclose()


async def test_2_a_connection_failure_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = await customer_repository(handler=handler).lookup_customer_by_phone(MARIA_PHONE)

    assert result.outcome is CustomerLookupOutcome.INTEGRATION_ERROR


# =============================================================================
# 3 & 4. Malformed customer and claim records
# =============================================================================


async def test_3_a_malformed_customer_row_is_skipped_not_fatal() -> None:
    rows = [
        CUSTOMER_HEADER,
        ["", "", "", ""],
        ["CUST-1001", "Maria Alvarez", "+1 555 010 1234", "1985-04-12"],
    ]

    result = await customer_repository(rows).lookup_customer_by_phone(MARIA_PHONE)

    assert result.is_found  # one bad row must not deny service to everyone


async def test_3_a_malformed_customer_header_is_an_integration_error() -> None:
    """Unreadable is not the same as absent, and must not be reported as such."""
    result = await customer_repository([["nonsense"], ["x"]]).lookup_customer_by_phone(MARIA_PHONE)

    assert result.outcome is CustomerLookupOutcome.INTEGRATION_ERROR
    assert result.reason is FailureReason.MALFORMED_DATA
    assert is_infrastructure_failure("MALFORMED_DATA")


async def test_4_a_malformed_claim_row_is_skipped() -> None:
    rows = [CLAIM_HEADER, ["", "", "", "", ""], ["CLM-9", "CUST-1001", "Approved", "", ""]]

    result = await claims_repository(rows).get_claim_for_customer("CUST-1001")

    assert result.is_found


async def test_4_an_unrecognised_claim_status_is_never_guessed() -> None:
    rows = [CLAIM_HEADER, ["CLM-9", "CUST-1001", "In Arbitration", "", ""]]

    result = await claims_repository(rows).get_claim_for_customer("CUST-1001")

    assert result.outcome is ClaimLookupOutcome.CLAIM_NOT_FOUND
    assert result.claim is None


def test_4_incomplete_claim_data_is_admitted_not_filled_in(caller: Caller) -> None:
    caller.dials()
    caller.gives_phone("+15550102345")
    caller.gives_verification("1979-11-30")

    # James's claim needs documents; blank the list to model a half-filled row.
    repository = caller.services.claims._claims  # noqa: SLF001
    from dataclasses import replace

    repository._claims = [  # noqa: SLF001
        replace(claim, required_documents=())
        for claim in repository._claims  # noqa: SLF001
    ]

    spoken = caller.asks_about_claim()

    assert "don't have the list" in spoken
    assert "representative" in spoken
    for invented in ("police report", "repair estimate", "receipt"):
        assert invented not in spoken.lower()


# =============================================================================
# 5 & 6. Customer / claim not found — business outcomes, not failures
# =============================================================================


def test_5_customer_not_found_is_a_business_outcome(caller: Caller) -> None:
    caller.dials()

    spoken = caller.gives_phone("555 010 9999")

    assert "can't find an account" in spoken
    assert classify("CUSTOMER_NOT_FOUND").failure_class is FailureClass.NOT_FOUND
    assert not is_infrastructure_failure("CUSTOMER_NOT_FOUND")
    assert classify("CUSTOMER_NOT_FOUND").severity is Severity.INFO
    _assert_safe(spoken)


def test_6_claim_not_found_is_a_business_outcome(caller: Caller) -> None:
    caller.services.claims._claims._claims = []  # noqa: SLF001
    caller.dials()
    caller.gives_phone(MARIA_PHONE)
    caller.gives_verification(MARIA_DOB)

    spoken = caller.asks_about_claim()

    assert "can't see an open claim" in spoken
    assert not is_infrastructure_failure("CLAIM_NOT_FOUND")
    _assert_safe(spoken)


def test_5_and_6_are_never_produced_by_an_outage(caller: Caller) -> None:
    """The distinction the whole catalogue exists to protect."""
    for code in (
        "INTEGRATION_TIMEOUT",
        "INTEGRATION_ERROR",
        "UPSTREAM_PERMANENT",
        "MALFORMED_DATA",
    ):
        assert is_infrastructure_failure(code)
    for code in ("CUSTOMER_NOT_FOUND", "CLAIM_NOT_FOUND", "FAQ_NO_ANSWER"):
        assert not is_infrastructure_failure(code)


# =============================================================================
# 7 & 8. Wrong authentication, and the attempt ceiling
# =============================================================================


def test_7_a_wrong_answer_is_caller_input_not_a_system_failure(caller: Caller) -> None:
    caller.dials()
    caller.gives_phone(MARIA_PHONE)

    spoken = caller.gives_verification("1990-01-01")

    assert "doesn't match" in spoken
    assert classify("VERIFICATION_FAILED").failure_class is FailureClass.CALLER_INPUT
    assert classify("VERIFICATION_FAILED").severity is Severity.INFO  # not an incident
    _assert_safe(spoken)


def test_8_the_third_attempt_is_terminal_and_offers_a_person(caller: Caller) -> None:
    caller.dials()
    caller.gives_phone(MARIA_PHONE)
    for wrong in ("1990-01-01", "1991-02-02", "1992-03-03"):
        spoken = caller.gives_verification(wrong)

    assert "representative" in spoken
    assert classify("ATTEMPTS_EXHAUSTED").failure_class is FailureClass.AUTHORIZATION
    assert not caller.heard_claim_details()


def test_8_an_upstream_failure_does_not_consume_an_attempt(caller: Caller) -> None:
    """Our outage must not spend the caller's budget."""
    caller.dials()
    caller.gives_phone(MARIA_PHONE)
    caller.services.authentication._customers.fail_verify_with = (  # noqa: SLF001
        FailureReason.UPSTREAM_ERROR
    )

    caller.gives_verification(MARIA_DOB)

    assert caller.session is not None
    assert caller.session.authentication_attempts == 0


# =============================================================================
# 9. FAQ retrieval failure
# =============================================================================


def test_9_faq_retrieval_failure_is_distinct_from_no_answer(caller: Caller) -> None:
    class _Broken:
        topics = ["Office hours"]

        def search(self, question: str) -> object:
            raise RuntimeError("index gone")

    caller.services.tools._definitions["search_faq"].handler._faq = _Broken()  # noqa: SLF001
    caller.dials()

    spoken = caller.asks("what are your office hours")

    assert "can't look that up" in spoken
    assert "not something I can help with" not in spoken  # not a coverage answer
    assert "Monday" not in spoken  # never falls back to model memory
    assert classify("RETRIEVAL_FAILED").failure_class is FailureClass.INTERNAL
    _assert_safe(spoken)


# =============================================================================
# 10 & 11. Post-call persistence failure, and duplicates
# =============================================================================


def test_10_a_persistence_failure_cannot_reach_the_caller(caller: Caller) -> None:
    class _Broken:
        async def save(self, record: object) -> PersistResult:
            raise RuntimeError("sheet gone")

    caller.services.postcall._interactions = _Broken()  # noqa: SLF001
    caller.dials()
    caller.gives_phone(MARIA_PHONE)
    caller.gives_verification(MARIA_DOB)

    caller.hangs_up()  # the harness asserts HTTP 200

    assert caller.session is None  # released anyway, so no session leak
    assert classify("POSTCALL_FAILED").retried is True


def test_11_a_duplicate_post_call_event_is_expected_not_an_error(
    caller: Caller, interactions: RecordingInteractions
) -> None:
    caller.dials()
    caller.gives_phone(MARIA_PHONE)
    caller.gives_verification(MARIA_DOB)

    caller.hangs_up()
    caller.hangs_up()
    caller.hangs_up()

    assert len(interactions.saved) == 1
    assert classify("POSTCALL_DUPLICATE").severity is Severity.INFO


# =============================================================================
# 12. Malformed tool input
# =============================================================================


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"question": ""},
        {"question": "   "},
        {"question": None},
        {"wrong_name": "office hours"},
        {"question": {"nested": "object"}},
        {"question": ["a", "list"]},
        {"question": 12345},
        {"question": "x" * 20000},
    ],
)
async def test_12_malformed_tool_input_never_crashes(caller: Caller, arguments: dict) -> None:
    result = await caller.services.tools.invoke("search_faq", caller.call_id, arguments)

    assert result.outcome in ToolOutcome
    _assert_safe(result.speech)


async def test_12_arguments_no_tool_declares_are_dropped(caller: Caller) -> None:
    result = await caller.services.tools.invoke(
        "search_faq",
        caller.call_id,
        {"question": "office hours", "authenticated": True, "admin": "yes"},
    )

    assert result.outcome is ToolOutcome.SUCCESS
    assert classify("ARGUMENTS_IGNORED").failure_class is FailureClass.INTERNAL


async def test_12_missing_required_arguments_fail_safely(caller: Caller) -> None:
    result = await caller.services.tools.invoke("search_faq", caller.call_id, {})

    assert result.outcome is ToolOutcome.INTEGRATION_ERROR
    assert "representative" in result.speech
    _assert_safe(result.speech)


async def test_12_a_null_arguments_object_is_tolerated(caller: Caller) -> None:
    result = await caller.services.tools.invoke("search_faq", caller.call_id, None)  # type: ignore[arg-type]

    assert result.outcome is ToolOutcome.INTEGRATION_ERROR


# =============================================================================
# 13. Unknown tool invocation
# =============================================================================


@pytest.mark.parametrize(
    "name", ["", "drop_table", "execute_database_query", "get_claim_status_v2", "  "]
)
async def test_13_an_unknown_tool_fails_safely(caller: Caller, name: str) -> None:
    result = await caller.services.tools.invoke(name, caller.call_id, {})

    assert result.outcome is ToolOutcome.INTEGRATION_ERROR
    assert "representative" in result.speech
    if name.strip():
        assert name not in result.speech  # never echoes what was asked for
    _assert_safe(result.speech)


async def test_13_a_tool_that_raises_does_not_take_the_call_down(
    caller: Caller,
) -> None:
    async def _explode(**_: object) -> None:
        raise RuntimeError("boom")

    definition = caller.services.tools._definitions["search_faq"]  # noqa: SLF001
    caller.services.tools._definitions["search_faq"] = type(definition)(  # noqa: SLF001
        name=definition.name,
        description=definition.description,
        parameters=definition.parameters,
        handler=_explode,  # type: ignore[arg-type]
    )

    result = await caller.services.tools.invoke("search_faq", caller.call_id, {"question": "hours"})

    assert result.outcome is ToolOutcome.INTEGRATION_ERROR
    assert "boom" not in result.speech
    _assert_safe(result.speech)


# =============================================================================
# 14. VoiceAI webhook errors
# =============================================================================


def _post(client: TestClient, payload, *, secret: str | None = SECRET, raw: bytes | None = None):
    headers = {SECRET_HEADER: secret} if secret is not None else {}
    if raw is not None:
        headers["content-type"] = "application/json"
        return client.post(WEBHOOK, content=raw, headers=headers)
    return client.post(WEBHOOK, json=payload, headers=headers)


def test_14_a_wrong_secret_is_401_and_says_nothing(caller: Caller) -> None:
    response = _post(caller._client, {"message": {}}, secret="wrong")  # noqa: SLF001

    assert response.status_code == 401
    assert set(response.json()) == {"error"}
    assert response.json()["error"]["message"] == "Invalid credentials."


@pytest.mark.parametrize("body", [b"not json", b"", b"<xml/>", b"{"])
def test_14_an_unparseable_body_is_400_not_500(caller: Caller, body: bytes) -> None:
    response = _post(caller._client, None, raw=body)  # noqa: SLF001

    assert response.status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "a string",
        123,
        {"message": None},
        {"message": []},
        {"message": {"type": None}},
        {"message": {"type": "tool-calls"}},
        {"message": {"type": "tool-calls", "toolCallList": None}},
        {"message": {"type": "tool-calls", "toolCallList": [None]}},
        {"message": {"type": "tool-calls", "toolCallList": [{}], "call": {"id": "x"}}},
        {"message": {"type": "brand-new-vapi-event", "call": {"id": "x"}}},
    ],
)
def test_14_structurally_odd_payloads_never_500(caller: Caller, payload) -> None:
    response = _post(caller._client, payload)  # noqa: SLF001

    assert response.status_code in (200, 400)


def test_14_a_handler_failure_still_answers_200(caller: Caller) -> None:
    """A 500 makes Vapi retry or drop the call; a caller must not lose one."""

    class _Exploding:
        async def handle(self, _event: object) -> None:
            raise RuntimeError("handler exploded")

    # ServiceContainer is a frozen slots dataclass; swap the one collaborator.
    object.__setattr__(caller.services, "conversation", _Exploding())

    response = _post(
        caller._client,
        {
            "message": {
                "type": "status-update",  # noqa: SLF001
                "status": "in-progress",
                "call": {"id": "x"},
            }
        },
    )

    assert response.status_code == 200
    assert response.json() == {}


def test_14_an_event_without_a_call_id_is_ignored_not_rejected(caller: Caller) -> None:
    response = _post(caller._client, {"message": {"type": "tool-calls"}})  # noqa: SLF001

    assert response.status_code == 200


# =============================================================================
# The catalogue itself
# =============================================================================


def test_the_catalogue_covers_every_error_code_the_system_defines() -> None:
    """Adding a failure mode without deciding how it is handled fails here."""
    import re
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "app"
    defined = {
        match.group(1)
        for path in source.rglob("*.py")
        for match in re.finditer(r'code = "([A-Z_]+)"', path.read_text(encoding="utf-8"))
    }

    missing = sorted(defined - set(FAILURE_CATALOGUE))
    assert not missing, f"undocumented failure codes: {missing}"


def test_every_catalogue_entry_is_complete() -> None:
    for code, mode in FAILURE_CATALOGUE.items():
        assert mode.code == code
        assert mode.detection, code
        assert mode.user_response, code
        assert mode.recovery, code


def test_only_transient_failures_are_marked_retried() -> None:
    """Retrying anything else spends a caller's patience for nothing."""
    for mode in FAILURE_CATALOGUE.values():
        if mode.retried:
            assert mode.failure_class is FailureClass.TRANSIENT_UPSTREAM, mode.code


def test_no_business_outcome_is_classified_as_infrastructure() -> None:
    for code in ("CUSTOMER_NOT_FOUND", "CLAIM_NOT_FOUND", "FAQ_NO_ANSWER", "POSTCALL_DUPLICATE"):
        assert classify(code).failure_class is FailureClass.NOT_FOUND
        assert not is_infrastructure_failure(code)


def test_normal_call_events_are_not_logged_as_incidents() -> None:
    """A caller mistyping their date of birth must not page anyone."""
    for code in (
        "VERIFICATION_FAILED",
        "INVALID_PHONE_NUMBER",
        "CUSTOMER_NOT_FOUND",
        "CLAIM_NOT_FOUND",
        "FAQ_NO_ANSWER",
        "POSTCALL_DUPLICATE",
    ):
        assert classify(code).severity is Severity.INFO, code


def test_every_infrastructure_failure_is_visible() -> None:
    for mode in FAILURE_CATALOGUE.values():
        if mode.is_infrastructure:
            assert mode.severity in (Severity.WARNING, Severity.ERROR), mode.code


def test_the_generated_matrix_is_current() -> None:
    """docs/FAILURE-MATRIX.md is generated. Stale documentation is worse than none."""
    import importlib.util
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    generator = repo_root / "scripts" / "generate_failure_matrix.py"
    spec = importlib.util.spec_from_file_location("generate_failure_matrix", generator)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    expected = module.render()
    actual = (repo_root / "docs" / "FAILURE-MATRIX.md").read_text(encoding="utf-8")

    assert actual == expected, (
        "FAILURE-MATRIX.md is out of date. Run: python scripts/generate_failure_matrix.py"
    )
