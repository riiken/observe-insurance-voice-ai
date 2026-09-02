"""Post-call processing (Integration #2): the record, the write, and its failures."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.integrations.repositories import (
    FailureReason,
    InteractionRepository,
    PersistOutcome,
    PersistResult,
)
from app.integrations.sheets.auth import ApiKeyAuthorizer
from app.integrations.sheets.client import GoogleSheetsClient
from app.integrations.sheets.interactions import GoogleSheetsInteractionRepository
from app.models.enums import (
    AuthenticationStatus,
    ConversationOutcome,
    EscalationReason,
    Sentiment,
)
from app.models.interaction import INTERACTION_COLUMNS, InteractionRecord
from app.models.session import SessionState
from app.services.postcall import PostCallService
from app.services.summary import ANONYMOUS_CALLER, score_sentiment, summarise
from tests.session_fixtures import MARIA

CALL = "call-abc-123"
INTERACTIONS_HEADER = list(INTERACTION_COLUMNS)


# --- fixtures -----------------------------------------------------------------


class RecordingInteractionRepository:
    """In-memory InteractionRepository with switchable failures."""

    def __init__(self) -> None:
        self.saved: list[InteractionRecord] = []
        self.fail_with: FailureReason | None = None
        self.raises = False

    async def save(self, record: InteractionRecord) -> PersistResult:
        if self.raises:
            raise RuntimeError("repository exploded")
        if self.fail_with is not None:
            return PersistResult.integration_error(self.fail_with)
        if any(existing.call_id == record.call_id for existing in self.saved):
            return PersistResult.already_recorded()
        self.saved.append(record)
        return PersistResult.persisted()


def _sheet_response(rows: list[list[str]]) -> httpx.Response:
    return httpx.Response(200, json={"values": rows})


def _append_response(rows: int = 1) -> httpx.Response:
    return httpx.Response(200, json={"updates": {"updatedRows": rows}})


def _repository(handler) -> GoogleSheetsInteractionRepository:
    client = GoogleSheetsClient(
        spreadsheet_id="interactions-sheet",
        authorizer=ApiKeyAuthorizer("test-key"),
        max_retries=0,
        backoff_base_seconds=0.0,
        transport=httpx.MockTransport(handler),
    )
    return GoogleSheetsInteractionRepository(client, cell_range="Interactions!A:L")


def _authenticated_session() -> SessionState:
    return (
        SessionState(call_id=CALL, caller_phone="+15550101234")
        .with_customer_found(MARIA)
        .with_authenticated(MARIA)
        .with_claim("CLM-88401")
        .with_outcome(ConversationOutcome.RESOLVED)
    )


# --- the record ---------------------------------------------------------------


def test_the_record_carries_every_required_field() -> None:
    record = PostCallService(None).build_record(_authenticated_session())

    assert record.caller_name == "Maria Alvarez"
    assert record.call_summary
    assert record.sentiment in Sentiment
    assert record.timestamp.tzinfo is not None


def test_the_record_carries_every_useful_field() -> None:
    record = PostCallService(None).build_record(_authenticated_session())

    assert record.call_id == CALL
    assert record.caller_phone == "+15550101234"
    assert record.customer_id == "CUST-1001"
    assert record.claim_id == "CLM-88401"
    assert record.authenticated is True
    assert record.resolution is ConversationOutcome.RESOLVED
    assert record.escalated is False
    assert record.escalation_reason is None


def test_missing_optional_fields_become_empty_cells_not_none() -> None:
    """A cell reading "None" looks like data to whoever opens the sheet."""
    record = PostCallService(None).build_record(SessionState(call_id=CALL))

    row = record.as_row()

    assert "None" not in row
    assert row[INTERACTION_COLUMNS.index("customer_id")] == ""
    assert row[INTERACTION_COLUMNS.index("claim_id")] == ""


def test_an_unidentified_caller_gets_a_readable_placeholder() -> None:
    record = PostCallService(None).build_record(SessionState(call_id=CALL))

    assert record.caller_name == ANONYMOUS_CALLER
    assert record.authenticated is False


def test_the_row_matches_the_column_order() -> None:
    record = PostCallService(None).build_record(_authenticated_session())

    row = record.as_row()

    assert len(row) == len(INTERACTION_COLUMNS)
    assert row[INTERACTION_COLUMNS.index("call_id")] == CALL
    assert row[INTERACTION_COLUMNS.index("authenticated")] == "TRUE"


# --- summary ------------------------------------------------------------------


def test_the_summary_describes_what_actually_happened() -> None:
    summary = summarise(_authenticated_session())

    assert "Maria Alvarez" in summary
    assert "CLM-88401" in summary


def test_the_summary_does_not_invent_a_claim_that_was_never_discussed() -> None:
    session = SessionState(call_id=CALL).with_customer_found(MARIA).with_authenticated(MARIA)

    summary = summarise(session)

    assert "claim" not in summary.lower()


def test_the_summary_records_a_failed_verification_honestly() -> None:
    session = SessionState(call_id=CALL).with_customer_found(MARIA)
    for _ in range(3):
        session = session.with_verification_failed()

    summary = summarise(session)

    assert "could not be verified" in summary
    assert "3 attempts" in summary


def test_the_summary_names_the_topics_actually_answered() -> None:
    session = (
        SessionState(call_id=CALL).with_faq_topic("Office hours").with_faq_topic("Mailing address")
    )

    summary = summarise(session)

    assert "office hours" in summary.lower()
    assert "mailing address" in summary.lower()


def test_the_summary_notes_an_escalation() -> None:
    session = SessionState(call_id=CALL).with_escalation(EscalationReason.CALLER_REQUEST)

    assert "Escalated to a representative" in summarise(session)


def test_the_summary_notes_an_emergency_distinctly() -> None:
    session = SessionState(call_id=CALL).with_escalation(EscalationReason.EMERGENCY)

    assert "emergency" in summarise(session).lower()


def test_the_summary_stays_short_enough_to_read_in_a_spreadsheet() -> None:
    session = _authenticated_session()

    assert len(summarise(session)) < 300


# --- sentiment ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("session", "expected"),
    [
        (_authenticated_session(), Sentiment.POSITIVE),
        (SessionState(call_id=CALL), Sentiment.NEUTRAL),
        (SessionState(call_id=CALL).with_customer_found(MARIA), Sentiment.NEUTRAL),
        (SessionState(call_id=CALL).with_customer_not_found(), Sentiment.NEGATIVE),
        (
            SessionState(call_id=CALL).with_escalation(EscalationReason.EMERGENCY),
            Sentiment.NEGATIVE,
        ),
        (
            SessionState(call_id=CALL).with_escalation(EscalationReason.SYSTEM_ERROR),
            Sentiment.NEGATIVE,
        ),
        (
            SessionState(call_id=CALL).with_escalation(EscalationReason.CALLER_REQUEST),
            Sentiment.NEUTRAL,
        ),
    ],
)
def test_sentiment_is_derived_from_outcome(session: SessionState, expected: Sentiment) -> None:
    assert score_sentiment(session) is expected


def test_a_locked_out_caller_is_negative() -> None:
    session = SessionState(call_id=CALL).with_customer_found(MARIA)
    for _ in range(3):
        session = session.with_verification_failed()

    assert session.authentication_status is AuthenticationStatus.AUTHENTICATION_FAILED
    assert score_sentiment(session) is Sentiment.NEGATIVE


def test_sentiment_only_ever_uses_the_controlled_vocabulary() -> None:
    sessions = [
        SessionState(call_id=CALL),
        _authenticated_session(),
        SessionState(call_id=CALL).with_customer_not_found(),
        SessionState(call_id=CALL).with_escalation(EscalationReason.EMERGENCY),
    ]

    for session in sessions:
        assert score_sentiment(session) in (
            Sentiment.POSITIVE,
            Sentiment.NEUTRAL,
            Sentiment.NEGATIVE,
        )


# --- writing to the sheet -----------------------------------------------------


async def test_a_successful_write_appends_one_row() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return _sheet_response([INTERACTIONS_HEADER])
        return _append_response()

    record = PostCallService(None).build_record(_authenticated_session())
    result = await _repository(handler).save(record)

    assert result.outcome is PersistOutcome.PERSISTED
    assert any(r.method == "POST" and ":append" in str(r.url) for r in requests)


async def test_values_are_written_raw_so_a_formula_stays_text() -> None:
    """A summary starting with "=" must be data, not a spreadsheet formula."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return (
            _sheet_response([INTERACTIONS_HEADER])
            if request.method == "GET"
            else _append_response()
        )

    await _repository(handler).save(PostCallService(None).build_record(_authenticated_session()))

    append = next(r for r in seen if r.method == "POST")
    assert append.url.params["valueInputOption"] == "RAW"


async def test_the_repository_satisfies_the_protocol() -> None:
    repository = _repository(lambda _r: _sheet_response([INTERACTIONS_HEADER]))

    assert isinstance(repository, InteractionRepository)


# --- idempotency --------------------------------------------------------------


async def test_a_repeated_call_is_not_written_twice() -> None:
    appends = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal appends
        if request.method == "GET":
            return _sheet_response([INTERACTIONS_HEADER])
        appends += 1
        return _append_response()

    repository = _repository(handler)
    record = PostCallService(None).build_record(_authenticated_session())

    first = await repository.save(record)
    second = await repository.save(record)

    assert first.outcome is PersistOutcome.PERSISTED
    assert second.outcome is PersistOutcome.ALREADY_RECORDED
    assert appends == 1


async def test_a_call_already_on_the_sheet_is_not_written_again() -> None:
    """Idempotency survives a restart: the sheet is the source of truth."""
    appends = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal appends
        if request.method == "GET":
            return _sheet_response(
                [INTERACTIONS_HEADER, [CALL, "2026-09-02T00:00:00+00:00", "Maria Alvarez"]]
            )
        appends += 1
        return _append_response()

    record = PostCallService(None).build_record(_authenticated_session())
    result = await _repository(handler).save(record)

    assert result.outcome is PersistOutcome.ALREADY_RECORDED
    assert appends == 0


async def test_a_different_call_is_still_written() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return _sheet_response([INTERACTIONS_HEADER, ["some-other-call", "", ""]])
        return _append_response()

    record = PostCallService(None).build_record(_authenticated_session())

    assert (await _repository(handler).save(record)).is_persisted


async def test_a_failed_write_can_be_retried_later() -> None:
    """A failure must not be mistaken for a duplicate on the next attempt."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.method == "GET":
            return _sheet_response([INTERACTIONS_HEADER])
        attempts += 1
        return _append_response() if attempts > 1 else httpx.Response(500)

    repository = _repository(handler)
    record = PostCallService(None).build_record(_authenticated_session())

    first = await repository.save(record)
    second = await repository.save(record)

    assert first.outcome is PersistOutcome.INTEGRATION_ERROR
    assert second.outcome is PersistOutcome.PERSISTED


# --- failures -----------------------------------------------------------------


async def test_a_transient_failure_is_retried_within_budget() -> None:
    appends = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal appends
        if request.method == "GET":
            return _sheet_response([INTERACTIONS_HEADER])
        appends += 1
        return _append_response() if appends > 2 else httpx.Response(503)

    client = GoogleSheetsClient(
        spreadsheet_id="s",
        authorizer=ApiKeyAuthorizer("k"),
        max_retries=3,
        backoff_base_seconds=0.0,
        transport=httpx.MockTransport(handler),
    )
    repository = GoogleSheetsInteractionRepository(client, cell_range="Interactions!A:L")

    result = await repository.save(PostCallService(None).build_record(_authenticated_session()))

    assert result.is_persisted
    assert appends == 3


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
async def test_a_permanent_failure_is_not_retried(status_code: int) -> None:
    appends = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal appends
        if request.method == "GET":
            return _sheet_response([INTERACTIONS_HEADER])
        appends += 1
        return httpx.Response(status_code)

    client = GoogleSheetsClient(
        spreadsheet_id="s",
        authorizer=ApiKeyAuthorizer("k"),
        max_retries=3,
        backoff_base_seconds=0.0,
        transport=httpx.MockTransport(handler),
    )
    repository = GoogleSheetsInteractionRepository(client, cell_range="Interactions!A:L")

    result = await repository.save(PostCallService(None).build_record(_authenticated_session()))

    assert result.outcome is PersistOutcome.INTEGRATION_ERROR
    assert appends == 1


async def test_a_timeout_is_reported_as_a_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return _sheet_response([INTERACTIONS_HEADER])
        raise httpx.ReadTimeout("slow", request=request)

    result = await _repository(handler).save(
        PostCallService(None).build_record(_authenticated_session())
    )

    assert result.outcome is PersistOutcome.INTEGRATION_ERROR
    assert result.reason is FailureReason.UPSTREAM_TIMEOUT


@pytest.mark.parametrize("body", [b"not json", b'{"no": "updates"}', b'"a string"'])
async def test_a_malformed_append_response_is_a_failure_not_a_success(
    body: bytes,
) -> None:
    """We must not claim a write we cannot confirm — the record would be lost."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return _sheet_response([INTERACTIONS_HEADER])
        return httpx.Response(200, content=body)

    result = await _repository(handler).save(
        PostCallService(None).build_record(_authenticated_session())
    )

    assert result.outcome is PersistOutcome.INTEGRATION_ERROR


async def test_an_unreadable_sheet_does_not_cause_a_duplicate_write() -> None:
    """If we cannot check for a duplicate, we do not write."""
    appends = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal appends
        if request.method == "GET":
            return httpx.Response(500)
        appends += 1
        return _append_response()

    result = await _repository(handler).save(
        PostCallService(None).build_record(_authenticated_session())
    )

    assert result.outcome is PersistOutcome.INTEGRATION_ERROR
    assert appends == 0


async def test_a_missing_call_id_column_is_an_integration_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return (
            _sheet_response([["timestamp", "caller_name"]])
            if request.method == "GET"
            else _append_response()
        )

    result = await _repository(handler).save(
        PostCallService(None).build_record(_authenticated_session())
    )

    assert result.outcome is PersistOutcome.INTEGRATION_ERROR
    assert result.reason is FailureReason.MALFORMED_DATA


# --- the service never breaks a call ------------------------------------------


async def test_the_service_never_raises_when_the_repository_fails() -> None:
    repository = RecordingInteractionRepository()
    repository.fail_with = FailureReason.UPSTREAM_ERROR

    result = await PostCallService(repository).record_call(_authenticated_session())

    assert result.persisted is False
    assert result.record.call_id == CALL


async def test_the_service_never_raises_when_the_repository_explodes() -> None:
    """The repository is contracted not to raise. If it does, we still cope."""
    repository = RecordingInteractionRepository()
    repository.raises = True

    result = await PostCallService(repository).record_call(_authenticated_session())

    assert result.persisted is False


async def test_an_unconfigured_integration_still_builds_and_logs_the_record() -> None:
    service = PostCallService(None)

    result = await service.record_call(_authenticated_session())

    assert service.configured is False
    assert result.persisted is False
    assert result.record.call_summary  # still derived, so the log has it


async def test_a_successful_record_reports_success() -> None:
    repository = RecordingInteractionRepository()

    result = await PostCallService(repository).record_call(_authenticated_session())

    assert result.persisted
    assert len(repository.saved) == 1
    assert repository.saved[0].sentiment is Sentiment.POSITIVE


def test_the_timestamp_is_timezone_aware() -> None:
    record = PostCallService(None).build_record(_authenticated_session())

    assert record.timestamp.tzinfo is not None
    assert record.timestamp <= datetime.now(tz=UTC)


# --- end to end, through the webhook ------------------------------------------


async def test_a_whole_call_produces_one_record() -> None:
    from app.integrations.voice_platform import parse_webhook
    from tests import voice_fixtures as vapi

    repository = RecordingInteractionRepository()
    services = vapi.build_container(interactions=repository)

    async def handle(payload: dict) -> None:
        await services.conversation.handle(parse_webhook(payload))

    await handle(vapi.call_started(phone=vapi.MARIA_PHONE))
    await handle(vapi.tool_call("lookup_customer", {"phone_number": vapi.MARIA_PHONE}))
    await handle(vapi.tool_call("verify_identity", {"verification_value": vapi.MARIA_DOB}))
    await handle(vapi.tool_call("get_claim_status", {}))
    await handle(vapi.end_of_call())

    assert len(repository.saved) == 1
    record = repository.saved[0]
    assert record.call_id == vapi.CALL_ID
    assert record.caller_name == "Maria Alvarez"
    assert record.authenticated is True
    assert record.claim_id == "CLM-88401"
    assert record.resolution is ConversationOutcome.RESOLVED
    assert record.sentiment is Sentiment.POSITIVE
    assert "Maria Alvarez" in record.call_summary


async def test_a_redelivered_end_of_call_does_not_duplicate() -> None:
    """Vapi retries webhooks; a second delivery must not add a second row."""
    from app.integrations.voice_platform import parse_webhook
    from tests import voice_fixtures as vapi

    repository = RecordingInteractionRepository()
    services = vapi.build_container(interactions=repository)

    await services.conversation.handle(parse_webhook(vapi.call_started()))
    await services.conversation.handle(parse_webhook(vapi.end_of_call()))
    await services.conversation.handle(parse_webhook(vapi.end_of_call()))

    assert len(repository.saved) == 1


async def test_a_persistence_failure_does_not_break_call_completion() -> None:
    """Failing to file paperwork must never affect the caller-facing flow."""
    from app.integrations.voice_platform import parse_webhook
    from tests import voice_fixtures as vapi

    repository = RecordingInteractionRepository()
    repository.raises = True
    services = vapi.build_container(interactions=repository)

    await services.conversation.handle(parse_webhook(vapi.call_started()))
    response = await services.conversation.handle(parse_webhook(vapi.end_of_call()))

    assert response is not None
    # The session is still released, so a failed write cannot leak sessions.
    assert await services.sessions.get(vapi.CALL_ID) is None


async def test_faq_topics_reach_the_record() -> None:
    from app.integrations.voice_platform import parse_webhook
    from tests import voice_fixtures as vapi

    repository = RecordingInteractionRepository()
    services = vapi.build_container(interactions=repository)

    await services.conversation.handle(parse_webhook(vapi.call_started()))
    await services.conversation.handle(
        parse_webhook(vapi.tool_call("search_faq", {"question": "what are your office hours"}))
    )
    await services.conversation.handle(parse_webhook(vapi.end_of_call()))

    assert "office hours" in repository.saved[0].call_summary.lower()
