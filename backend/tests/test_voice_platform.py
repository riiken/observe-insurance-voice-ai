"""Parsing and formatting Vapi payloads — the provider boundary."""

from __future__ import annotations

import pytest

from app.integrations.voice_platform import (
    VoiceEventType,
    acknowledgement,
    format_tool_results,
    parse_webhook,
    tool_schemas,
    verify_secret,
)
from app.tools.registry import ToolDefinition, ToolParameter
from tests import voice_fixtures as vapi

# --- secret verification ------------------------------------------------------


def test_a_matching_secret_is_accepted() -> None:
    assert verify_secret("s3cret", "s3cret") is True


@pytest.mark.parametrize("supplied", [None, "", "wrong", "s3cre", "s3crett"])
def test_a_wrong_or_missing_secret_is_rejected(supplied: str | None) -> None:
    assert verify_secret(supplied, "s3cret") is False


def test_no_configured_secret_accepts_anything() -> None:
    """Local development only — Settings refuses to start production this way."""
    assert verify_secret(None, None) is True


# --- event parsing ------------------------------------------------------------


def test_a_status_update_starts_a_call() -> None:
    parsed = parse_webhook(vapi.call_started(phone=vapi.MARIA_PHONE))

    assert parsed.event_type is VoiceEventType.CALL_STARTED
    assert parsed.call_id == vapi.CALL_ID
    assert parsed.caller_phone == vapi.MARIA_PHONE


def test_an_assistant_request_starts_a_call() -> None:
    assert parse_webhook(vapi.assistant_request()).event_type is VoiceEventType.CALL_STARTED


def test_the_current_tool_call_shape_is_parsed() -> None:
    parsed = parse_webhook(vapi.tool_call("search_faq", {"question": "office hours?"}))

    assert parsed.event_type is VoiceEventType.TOOL_CALLS
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "search_faq"
    assert parsed.tool_calls[0].arguments == {"question": "office hours?"}
    assert parsed.tool_calls[0].invocation_id == "toolcall-1"


def test_the_older_nested_function_shape_is_parsed() -> None:
    """Vapi has changed this shape; a payload change must not drop a call."""
    parsed = parse_webhook(vapi.legacy_tool_call("search_faq", {"question": "hours"}))

    assert parsed.tool_calls[0].name == "search_faq"
    assert parsed.tool_calls[0].arguments == {"question": "hours"}


def test_the_legacy_function_call_shape_is_parsed() -> None:
    parsed = parse_webhook(vapi.function_call("search_faq", {"question": "hours"}))

    assert parsed.tool_calls[0].name == "search_faq"
    assert parsed.tool_calls[0].arguments == {"question": "hours"}


def test_arguments_arriving_as_a_json_string_are_decoded() -> None:
    """Models sometimes emit arguments as a string rather than an object."""
    parsed = parse_webhook(vapi.tool_call("search_faq", '{"question": "office hours"}'))

    assert parsed.tool_calls[0].arguments == {"question": "office hours"}


def test_unparseable_arguments_become_empty_rather_than_raising() -> None:
    parsed = parse_webhook(vapi.tool_call("search_faq", "{not json"))

    assert parsed.tool_calls[0].arguments == {}


def test_multiple_tool_calls_are_all_returned() -> None:
    payload = vapi.tool_call("search_faq", {"question": "hours"})
    payload["message"]["toolCallList"].append(
        {"id": "toolcall-2", "name": "request_representative", "arguments": {}}
    )

    parsed = parse_webhook(payload)

    assert [invocation.name for invocation in parsed.tool_calls] == [
        "search_faq",
        "request_representative",
    ]


def test_an_end_of_call_report_ends_the_call() -> None:
    parsed = parse_webhook(vapi.end_of_call())

    assert parsed.event_type is VoiceEventType.CALL_ENDED
    assert parsed.ended_reason == "customer-ended-call"
    assert parsed.summary is not None


def test_a_status_update_of_ended_also_ends_the_call() -> None:
    payload = {
        "message": {"type": "status-update", "status": "ended", "call": {"id": vapi.CALL_ID}}
    }

    assert parse_webhook(payload).event_type is VoiceEventType.CALL_ENDED


@pytest.mark.parametrize(
    "raw_type", ["transcript", "speech-update", "conversation-update", "hang", "brand-new-type"]
)
def test_events_we_do_not_act_on_are_ignored_not_rejected(raw_type: str) -> None:
    """Vapi adds message types; an unknown one must not drop a live call."""
    payload = {"message": {"type": raw_type, "call": {"id": vapi.CALL_ID}}}

    assert parse_webhook(payload).event_type is VoiceEventType.IGNORED


@pytest.mark.parametrize("payload", [{}, {"message": None}, {"message": "text"}, {"message": {}}])
def test_malformed_payloads_parse_to_ignored(payload: dict) -> None:
    assert parse_webhook(payload).event_type is VoiceEventType.IGNORED


def test_an_unwrapped_message_is_tolerated() -> None:
    """Some Vapi versions post the message without the outer envelope."""
    payload = {"type": "end-of-call-report", "call": {"id": vapi.CALL_ID}}

    assert parse_webhook(payload).event_type is VoiceEventType.CALL_ENDED


# --- response formatting ------------------------------------------------------


def test_tool_results_use_the_shape_vapi_expects() -> None:
    body = format_tool_results({"toolcall-1": "Your claim is under review."})

    assert body == {
        "results": [{"toolCallId": "toolcall-1", "result": "Your claim is under review."}]
    }


def test_an_acknowledgement_is_an_empty_object() -> None:
    assert acknowledgement() == {}


# --- assistant configuration --------------------------------------------------


def test_tool_schemas_render_in_vapi_format() -> None:
    async def _handler(**_: object) -> None: ...

    definition = ToolDefinition(
        name="search_faq",
        description="Answer a general question.",
        parameters=(ToolParameter(name="question", description="The question."),),
        handler=_handler,  # type: ignore[arg-type]
    )

    schema = tool_schemas([definition])[0]

    assert schema["type"] == "function"
    assert schema["function"]["name"] == "search_faq"
    assert schema["function"]["parameters"]["required"] == ["question"]
    assert "question" in schema["function"]["parameters"]["properties"]
