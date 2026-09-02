"""The webhook endpoint: authentication, response shape, and failure behaviour."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.integrations.voice_platform import SECRET_HEADER
from app.main import create_app
from tests import voice_fixtures as vapi
from tests.voice_fixtures import CALL_ID, MARIA_DOB, MARIA_PHONE

SECRET = "webhook-s3cret"
WEBHOOK = "/api/v1/voice/webhook"


def _settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        google_sheets_api_key="test-key",
        google_sheets_spreadsheet_id="sheet-1",
        voice_platform_api_key=SECRET,
        **overrides,
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A real app, with the Sheets repositories swapped for fakes at startup."""
    app = create_app(_settings())

    with TestClient(app, raise_server_exceptions=False) as test_client:
        app.state.services = vapi.build_container()
        yield test_client


def _post(client: TestClient, payload: dict, *, secret: str | None = SECRET):
    headers = {SECRET_HEADER: secret} if secret is not None else {}
    return client.post(WEBHOOK, json=payload, headers=headers)


def _speak(client: TestClient, tool: str, **arguments: object) -> str:
    response = _post(client, vapi.tool_call(tool, arguments))
    assert response.status_code == 200
    return response.json()["results"][0]["result"]


# --- webhook authentication ---------------------------------------------------


def test_a_valid_secret_is_accepted(client: TestClient) -> None:
    assert _post(client, vapi.call_started()).status_code == 200


@pytest.mark.parametrize("secret", [None, "", "wrong-secret"])
def test_a_missing_or_wrong_secret_is_rejected(client: TestClient, secret: str | None) -> None:
    response = _post(client, vapi.call_started(), secret=secret)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_an_unauthenticated_request_does_no_work(client: TestClient) -> None:
    """A rejected webhook must not create a session."""
    _post(client, vapi.call_started(), secret="wrong")

    response = _post(client, vapi.tool_call("get_claim_status", {}))
    assert "confirm who I'm speaking with" in response.json()["results"][0]["result"]


def test_the_rejection_reveals_nothing(client: TestClient) -> None:
    body = _post(client, vapi.call_started(), secret="wrong").json()

    assert set(body) == {"error"}
    assert body["error"]["message"] == "Invalid credentials."


# --- response shape -----------------------------------------------------------


def test_a_tool_call_returns_the_vapi_result_shape(client: TestClient) -> None:
    _post(client, vapi.call_started())

    body = _post(client, vapi.tool_call("search_faq", {"question": "office hours"})).json()

    assert list(body) == ["results"]
    assert body["results"][0]["toolCallId"] == "toolcall-1"
    assert "Monday" in body["results"][0]["result"]


def test_a_lifecycle_event_is_acknowledged_with_an_empty_body(client: TestClient) -> None:
    assert _post(client, vapi.call_started()).json() == {}
    assert _post(client, vapi.end_of_call()).json() == {}


def test_an_ignored_event_is_still_a_200(client: TestClient) -> None:
    """Returning an error for an unrecognised message would drop the call."""
    payload = {"message": {"type": "transcript", "call": {"id": CALL_ID}}}

    assert _post(client, payload).status_code == 200


def test_a_response_never_contains_json_for_the_caller_to_read(
    client: TestClient,
) -> None:
    _post(client, vapi.call_started())

    spoken = _speak(client, "search_faq", question="what are your office hours")

    for artefact in ("{", "}", "[", "]", '"outcome"'):
        assert artefact not in spoken


# --- malformed input ----------------------------------------------------------


def test_a_body_that_is_not_json_is_rejected_cleanly(client: TestClient) -> None:
    response = client.post(WEBHOOK, content=b"not json", headers={SECRET_HEADER: SECRET})

    assert response.status_code == 400


def test_a_json_body_that_is_not_an_object_is_rejected_cleanly(
    client: TestClient,
) -> None:
    response = client.post(WEBHOOK, json=["a", "list"], headers={SECRET_HEADER: SECRET})

    assert response.status_code == 400


@pytest.mark.parametrize("payload", [{}, {"message": {}}, {"message": {"type": "unknown"}}])
def test_structurally_odd_payloads_do_not_error(client: TestClient, payload: dict) -> None:
    assert _post(client, payload).status_code == 200


# --- the whole call over HTTP -------------------------------------------------


def test_a_full_call_through_the_webhook(client: TestClient) -> None:
    assert _post(client, vapi.call_started(phone=MARIA_PHONE)).status_code == 200

    assert "Maria" in _speak(client, "lookup_customer", phone_number="555 010 1234")
    assert "verified" in _speak(client, "verify_identity", verification_value=MARIA_DOB).lower()
    assert "under review" in _speak(client, "get_claim_status").lower()

    assert _post(client, vapi.end_of_call()).status_code == 200


def test_call_id_flows_from_the_payload_into_the_session(client: TestClient) -> None:
    """Propagation: the session a tool touches is the one the platform named."""
    _post(client, vapi.call_started(call_id="other-call"))
    _post(
        client,
        vapi.tool_call("lookup_customer", {"phone_number": MARIA_PHONE}, call_id="other-call"),
    )

    # A different call is a different session, still unauthenticated.
    assert "confirm who I'm speaking with" in _speak(client, "get_claim_status")


# --- availability -------------------------------------------------------------


def test_the_webhook_reports_503_when_the_integration_is_absent() -> None:
    app = create_app(Settings(_env_file=None, voice_platform_api_key=SECRET))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(WEBHOOK, json=vapi.call_started(), headers={SECRET_HEADER: SECRET})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


def test_the_secret_is_checked_before_service_availability() -> None:
    """Otherwise 401-vs-503 tells an unauthenticated caller how we are configured."""
    app = create_app(Settings(_env_file=None, voice_platform_api_key=SECRET))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(WEBHOOK, json=vapi.call_started(), headers={SECRET_HEADER: "wrong"})

    assert response.status_code == 401


def test_the_assistant_config_lists_exactly_the_five_tools(client: TestClient) -> None:
    body = client.get("/api/v1/voice/assistant-config").json()

    names = {tool["function"]["name"] for tool in body["model"]["tools"]}
    assert names == {
        "lookup_customer",
        "verify_identity",
        "get_claim_status",
        "search_faq",
        "request_representative",
    }


def test_the_assistant_config_carries_the_greeting(client: TestClient) -> None:
    """The caller is greeted before any webhook fires, so the greeting is config."""
    body = client.get("/api/v1/voice/assistant-config").json()

    greeting = body["firstMessage"]
    assert "Observe Insurance" in greeting
    assert "claims assistant" in greeting
    assert greeting.rstrip().endswith("?")  # opens with one question
    assert len(greeting.split()) <= 30  # short enough to hear


def test_the_assistant_config_carries_the_system_prompt(client: TestClient) -> None:
    body = client.get("/api/v1/voice/assistant-config").json()

    prompt = body["model"]["messages"][0]["content"]
    assert "Observe Insurance" in prompt
    assert "The backend decides who is verified" in prompt
