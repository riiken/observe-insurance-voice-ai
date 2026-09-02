"""Structured events, metrics, and what must never appear in either."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core import events
from app.core.config import Settings
from app.core.logging import configure_logging
from app.core.metrics import METRICS, MetricsRegistry
from app.main import create_app
from app.services.container import build_services
from tests.conversation_harness import SECRET, Caller, RecordingInteractions
from tests.voice_fixtures import FakeIntegration

MARIA_PHONE = "+15550101234"
MARIA_DOB = "1985-04-12"

REQUIRED_EVENTS = (
    events.CALL_STARTED,
    events.CUSTOMER_LOOKUP_STARTED,
    events.CUSTOMER_LOOKUP_COMPLETED,
    events.AUTHENTICATION_SUCCESS,
    events.AUTHENTICATION_FAILED,
    events.CLAIM_LOOKUP,
    events.FAQ_LOOKUP,
    events.ESCALATION_REQUESTED,
    events.TOOL_ERROR,
    events.CALL_COMPLETED,
    events.POSTCALL_PERSISTED,
    events.POSTCALL_FAILED,
)


@pytest.fixture
def captured_logs() -> Iterator[list[dict]]:
    """JSON log records emitted during the test."""
    buffer = io.StringIO()
    configure_logging(level="DEBUG", log_format="json", stream=buffer)
    records: list[dict] = []
    yield records
    records.extend(json.loads(line) for line in buffer.getvalue().splitlines() if line.strip())
    configure_logging(level="INFO", log_format="console", stream=io.StringIO())


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


def _names(records: list[dict]) -> set[str]:
    return {record.get("event", "") for record in records}


# --- structured events --------------------------------------------------------


def test_every_required_event_name_is_defined() -> None:
    for name in REQUIRED_EVENTS:
        assert name in events.ALL_EVENTS


def test_a_whole_call_emits_the_lifecycle_events(caller: Caller, captured_logs: list[dict]) -> None:
    caller.dials(phone=MARIA_PHONE)
    caller.gives_phone(MARIA_PHONE)
    caller.gives_verification(MARIA_DOB)
    caller.asks_about_claim()
    caller.asks("what are your office hours")
    caller.hangs_up()

    configure_logging(level="INFO", log_format="console", stream=io.StringIO())


def test_the_lookup_pair_is_emitted_with_timing(caller: Caller) -> None:
    buffer = io.StringIO()
    configure_logging(level="DEBUG", log_format="json", stream=buffer)
    try:
        caller.dials()
        caller.gives_phone(MARIA_PHONE)
    finally:
        configure_logging(level="INFO", log_format="console", stream=io.StringIO())

    records = [json.loads(line) for line in buffer.getvalue().splitlines() if line]
    names = _names(records)

    assert events.CUSTOMER_LOOKUP_STARTED in names
    assert events.CUSTOMER_LOOKUP_COMPLETED in names

    completed = next(r for r in records if r["event"] == events.CUSTOMER_LOOKUP_COMPLETED)
    assert "duration_ms" in completed
    assert completed["success"] is True
    assert completed["outcome"] == "CUSTOMER_FOUND"


def test_every_event_during_a_call_carries_the_call_id(caller: Caller) -> None:
    """One filter, one call. Without this, correlating a live incident is guesswork."""
    buffer = io.StringIO()
    configure_logging(level="DEBUG", log_format="json", stream=buffer)
    try:
        caller.dials()
        caller.gives_phone(MARIA_PHONE)
        caller.gives_verification(MARIA_DOB)
        caller.asks_about_claim()
        caller.hangs_up()
    finally:
        configure_logging(level="INFO", log_format="console", stream=io.StringIO())

    records = [json.loads(line) for line in buffer.getvalue().splitlines() if line]
    domain = [
        r
        for r in records
        if r["event"].startswith(
            ("call.", "customer.", "authentication.", "claim.", "tool.", "postcall.")
        )
    ]

    assert domain
    for record in domain:
        assert record.get("call_id"), f"{record['event']} has no call_id"


def test_a_completed_call_reports_its_duration(caller: Caller) -> None:
    buffer = io.StringIO()
    configure_logging(level="DEBUG", log_format="json", stream=buffer)
    try:
        caller.dials()
        caller.hangs_up()
    finally:
        configure_logging(level="INFO", log_format="console", stream=io.StringIO())

    records = [json.loads(line) for line in buffer.getvalue().splitlines() if line]
    completed = next(r for r in records if r["event"] == events.CALL_COMPLETED)

    assert completed["duration_ms"] >= 0
    assert completed["outcome"]


# --- what logs must never contain --------------------------------------------


def test_logs_never_contain_the_verification_value(caller: Caller) -> None:
    buffer = io.StringIO()
    configure_logging(level="DEBUG", log_format="json", stream=buffer)
    try:
        caller.dials()
        caller.gives_phone(MARIA_PHONE)
        caller.gives_verification(MARIA_DOB)
        caller.hangs_up()
    finally:
        configure_logging(level="INFO", log_format="console", stream=io.StringIO())

    assert MARIA_DOB not in buffer.getvalue()


def test_logs_never_contain_a_full_phone_number(caller: Caller) -> None:
    buffer = io.StringIO()
    configure_logging(level="DEBUG", log_format="json", stream=buffer)
    try:
        caller.dials(phone=MARIA_PHONE)
        caller.gives_phone(MARIA_PHONE)
        caller.gives_verification(MARIA_DOB)
        caller.hangs_up()
    finally:
        configure_logging(level="INFO", log_format="console", stream=io.StringIO())

    assert MARIA_PHONE not in buffer.getvalue()


def test_a_failed_lookup_logs_a_redacted_number(caller: Caller) -> None:
    """'Which number did not match' is the first thing anyone asks — but the
    last four digits are enough to answer it."""
    buffer = io.StringIO()
    configure_logging(level="DEBUG", log_format="json", stream=buffer)
    try:
        caller.dials()
        caller.gives_phone("555 010 9999")
    finally:
        configure_logging(level="INFO", log_format="console", stream=io.StringIO())

    output = buffer.getvalue()
    assert "+15550109999" not in output
    assert "***9999" in output


def test_a_successful_lookup_does_not_log_the_number_at_all(caller: Caller) -> None:
    """Once identified, the customer id is the better key and the phone is PII."""
    buffer = io.StringIO()
    configure_logging(level="DEBUG", log_format="json", stream=buffer)
    try:
        caller.dials()
        caller.gives_phone(MARIA_PHONE)
    finally:
        configure_logging(level="INFO", log_format="console", stream=io.StringIO())

    output = buffer.getvalue()
    assert "***1234" not in output
    assert "CUST-1001" in output


def test_logs_never_contain_the_customer_name(caller: Caller) -> None:
    """An id is enough to join to the customer sheet; a name is PII in a log store."""
    buffer = io.StringIO()
    configure_logging(level="DEBUG", log_format="json", stream=buffer)
    try:
        caller.dials()
        caller.gives_phone(MARIA_PHONE)
        caller.gives_verification(MARIA_DOB)
        caller.hangs_up()
    finally:
        configure_logging(level="INFO", log_format="console", stream=io.StringIO())

    assert "Alvarez" not in buffer.getvalue()


def test_the_http_client_cannot_log_a_credential() -> None:
    configure_logging(level="DEBUG", log_format="json", stream=io.StringIO())

    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


# --- metrics ------------------------------------------------------------------


def test_a_call_is_counted_once(caller: Caller) -> None:
    caller.dials()
    caller.dials()  # a redelivered start must not double-count

    assert METRICS.snapshot()["counters"]["calls_total"] == 1


def test_tool_latency_and_outcomes_are_recorded(caller: Caller) -> None:
    caller.dials()
    caller.asks("what are your office hours")

    snapshot = METRICS.snapshot()

    assert snapshot["counters"]["tool_calls_total{outcome=SUCCESS,tool=search_faq}"] == 1
    latency = snapshot["latencies"]["tool_latency_ms{tool=search_faq}"]
    assert latency["count"] == 1
    assert latency["mean_ms"] >= 0


def test_authentication_success_rate_is_derived(caller: Caller) -> None:
    caller.dials()
    caller.gives_phone(MARIA_PHONE)
    caller.gives_verification("1990-01-01")
    caller.gives_verification(MARIA_DOB)

    rates = METRICS.snapshot()["rates"]

    assert rates["authentication_success_rate"] == 0.5


def test_an_attempt_is_counted_once_not_once_per_layer(caller: Caller) -> None:
    """The service owns this counter; the adapter used to double-count it."""
    caller.dials()
    caller.gives_phone(MARIA_PHONE)
    caller.gives_verification(MARIA_DOB)

    counters = METRICS.snapshot()["counters"]

    assert counters["authentication_attempts_total{outcome=success}"] == 1


def test_tool_success_rate_sums_across_tools(caller: Caller) -> None:
    """Keys carry a `tool` label too, so an exact-key lookup reads zero."""
    caller.dials()
    caller.gives_phone(MARIA_PHONE)
    caller.gives_verification(MARIA_DOB)
    caller.asks_about_claim()

    rates = METRICS.snapshot()["rates"]

    assert rates["tool_success_rate"] == 1.0


def test_a_mixed_set_of_tool_outcomes_gives_a_partial_rate(caller: Caller) -> None:
    caller.dials()
    caller.asks_about_claim()  # NOT_AUTHORIZED
    caller.asks("what are your office hours")  # SUCCESS

    assert METRICS.snapshot()["rates"]["tool_success_rate"] == 0.5


def test_escalation_rate_is_derived(caller: Caller) -> None:
    caller.dials()
    caller.asks_for_a_person()

    assert METRICS.snapshot()["rates"]["escalation_rate"] == 1.0


def test_post_call_persistence_rate_is_derived(caller: Caller) -> None:
    caller.dials()
    caller.hangs_up()

    assert METRICS.snapshot()["rates"]["postcall_persistence_success_rate"] == 1.0


def test_call_duration_is_recorded(caller: Caller) -> None:
    caller.dials()
    caller.hangs_up()

    assert METRICS.snapshot()["latencies"]["call_duration_ms"]["count"] == 1


def test_a_rate_with_no_data_is_none_not_zero() -> None:
    """Zero percent and 'nothing has happened' are different on a dashboard."""
    registry = MetricsRegistry()

    assert registry.snapshot()["rates"]["authentication_success_rate"] is None


def test_latency_summary_tracks_the_useful_numbers() -> None:
    registry = MetricsRegistry()
    for duration in (10.0, 20.0, 60.0):
        registry.observe("thing_ms", duration)

    summary = registry.snapshot()["latencies"]["thing_ms"]

    assert summary["count"] == 3
    assert summary["mean_ms"] == 30.0
    assert summary["min_ms"] == 10.0
    assert summary["max_ms"] == 60.0


# --- the endpoint -------------------------------------------------------------


def test_the_metrics_endpoint_serves_the_snapshot(caller: Caller) -> None:
    caller.dials()
    caller.asks("office hours")

    body = caller._client.get("/metrics").json()  # noqa: SLF001

    assert set(body) == {"counters", "latencies", "rates"}
    assert body["counters"]["calls_total"] == 1


def test_the_metrics_endpoint_exposes_no_identifiers(caller: Caller) -> None:
    """Counts and durations only — nothing that identifies a caller."""
    caller.dials(phone=MARIA_PHONE)
    caller.gives_phone(MARIA_PHONE)
    caller.gives_verification(MARIA_DOB)
    caller.asks_about_claim()

    rendered = caller._client.get("/metrics").text  # noqa: SLF001

    for identifier in (
        MARIA_PHONE,
        MARIA_DOB,
        "CUST-1001",
        "CLM-88401",
        caller.call_id,
        "Alvarez",
        SECRET,
    ):
        assert identifier not in rendered


def test_metrics_hold_nothing_per_call(caller: Caller) -> None:
    """The registry must not grow with call volume."""
    for index in range(25):
        caller.call_id = f"call-{index}"
        caller.dials()
        caller.hangs_up()

    snapshot = METRICS.snapshot()

    # A handful of keys regardless of how many calls went through.
    assert len(snapshot["counters"]) < 15
    assert len(snapshot["latencies"]) < 15
