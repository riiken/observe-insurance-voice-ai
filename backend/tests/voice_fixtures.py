"""Vapi webhook payloads and a fully wired service container, without a network.

The provider is mocked by constructing the payloads it sends — parsing, dispatch
and the response shape all run for real, so a change to any of them is caught.
"""

from __future__ import annotations

from typing import Any

from app.services.container import ServiceContainer, build_services
from tests.session_fixtures import FakeClaimsRepository, FakeCustomerRepository

CALL_ID = "vapi-call-0001"
MARIA_PHONE = "+15550101234"
MARIA_DOB = "1985-04-12"
JAMES_PHONE = "+15550102345"
JAMES_DOB = "1979-11-30"


class FakeIntegration:
    """Stands in for `DataIntegration` with in-memory repositories."""

    def __init__(self) -> None:
        self.customers = FakeCustomerRepository()
        self.claims = FakeClaimsRepository()


def build_container() -> ServiceContainer:
    """A real container — real tools, real services, fake repositories."""
    return build_services(FakeIntegration())  # type: ignore[arg-type]


# --- Vapi payload builders ----------------------------------------------------


def call_started(call_id: str = CALL_ID, phone: str | None = None) -> dict[str, Any]:
    return {
        "message": {
            "type": "status-update",
            "status": "in-progress",
            "call": _call(call_id, phone),
        }
    }


def assistant_request(call_id: str = CALL_ID, phone: str | None = None) -> dict[str, Any]:
    return {"message": {"type": "assistant-request", "call": _call(call_id, phone)}}


def tool_call(
    name: str,
    arguments: dict[str, Any] | str | None = None,
    *,
    call_id: str = CALL_ID,
    invocation_id: str = "toolcall-1",
) -> dict[str, Any]:
    """Current Vapi shape: message.toolCallList."""
    return {
        "message": {
            "type": "tool-calls",
            "call": _call(call_id),
            "toolCallList": [
                {
                    "id": invocation_id,
                    "name": name,
                    "arguments": {} if arguments is None else arguments,
                }
            ],
        }
    }


def legacy_tool_call(
    name: str, arguments: dict[str, Any], *, call_id: str = CALL_ID
) -> dict[str, Any]:
    """Older Vapi shape: message.toolCalls with a nested function object."""
    return {
        "message": {
            "type": "tool-calls",
            "call": _call(call_id),
            "toolCalls": [
                {"id": "toolcall-legacy", "function": {"name": name, "arguments": arguments}}
            ],
        }
    }


def function_call(
    name: str, parameters: dict[str, Any], *, call_id: str = CALL_ID
) -> dict[str, Any]:
    """Oldest Vapi shape: message.functionCall."""
    return {
        "message": {
            "type": "function-call",
            "call": _call(call_id),
            "functionCall": {"name": name, "parameters": parameters},
        }
    }


def end_of_call(
    call_id: str = CALL_ID, *, ended_reason: str = "customer-ended-call"
) -> dict[str, Any]:
    return {
        "message": {
            "type": "end-of-call-report",
            "call": _call(call_id),
            "endedReason": ended_reason,
            "summary": "The caller asked about their claim.",
            "transcript": "AI: Thanks for calling...",
        }
    }


def _call(call_id: str, phone: str | None = None) -> dict[str, Any]:
    call: dict[str, Any] = {"id": call_id}
    if phone is not None:
        call["customer"] = {"number": phone}
    return call
