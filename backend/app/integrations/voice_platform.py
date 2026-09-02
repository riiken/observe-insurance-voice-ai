"""Vapi integration — the only module that knows which voice platform we use.

Everything above this file speaks in `VoiceEvent` and `ToolInvocation`. Swapping
Vapi for another provider means rewriting this module and nothing else: the
webhook route, the tool registry and every service stay as they are.

Vapi posts a single webhook for everything, wrapped as `{"message": {...}}` with
a `type` discriminator. The shapes below have varied across Vapi versions
(`toolCallList`, `toolCalls`, the older `functionCall`), so parsing accepts all
three rather than pinning to whichever one is current — a payload change should
not take a phone line down.

Docs: https://docs.vapi.ai/server-url
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.logging import event, get_logger
from app.tools.registry import ToolDefinition

log = get_logger(__name__)

PLATFORM_NAME = "vapi"

# Vapi sends the assistant's configured `serverUrlSecret` in this header.
SECRET_HEADER = "x-vapi-secret"


class VoiceEventType(StrEnum):
    """Provider-neutral event vocabulary."""

    CALL_STARTED = "CALL_STARTED"
    TOOL_CALLS = "TOOL_CALLS"
    CALL_ENDED = "CALL_ENDED"
    # Received, acknowledged, and deliberately not acted on.
    IGNORED = "IGNORED"


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """One tool the model wants to run."""

    invocation_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VoiceEvent:
    """A webhook payload, translated out of the provider's vocabulary."""

    event_type: VoiceEventType
    call_id: str
    raw_type: str
    caller_phone: str | None = None
    tool_calls: tuple[ToolInvocation, ...] = ()
    ended_reason: str | None = None
    summary: str | None = None
    transcript: str | None = None


# Vapi message types we act on. Everything else — transcripts, speech updates,
# conversation updates — is acknowledged and dropped: acting on a partial
# transcript would mean reacting to half a sentence.
_STARTED_TYPES = frozenset({"assistant-request", "call.started"})
_TOOL_TYPES = frozenset({"tool-calls", "function-call", "tool_calls"})
_ENDED_TYPES = frozenset({"end-of-call-report"})


def verify_secret(supplied: str | None, expected: str | None) -> bool:
    """Constant-time check of the shared secret Vapi sends.

    An unset expectation accepts anything, which is only tolerable in local
    development — `Settings` refuses to start a production environment without
    a secret configured, so this cannot silently be the deployed behaviour.
    """
    if not expected:
        return True
    if not supplied:
        return False
    return hmac.compare_digest(supplied, expected)


def parse_webhook(payload: dict[str, Any]) -> VoiceEvent:
    """Translate a Vapi webhook body into a `VoiceEvent`.

    Never raises on a shape it does not recognise: an unknown message becomes
    `IGNORED`, because rejecting a payload Vapi decided to add would drop a live
    call for no benefit.
    """
    raw_message = payload.get("message")
    message: dict[str, Any] = (
        raw_message
        if isinstance(raw_message, dict)
        # Some Vapi versions post the message unwrapped.
        else (payload if isinstance(payload, dict) else {})
    )

    raw_type = str(message.get("type") or "")
    raw_call = message.get("call")
    call: dict[str, Any] = raw_call if isinstance(raw_call, dict) else {}
    call_id = str(call.get("id") or message.get("callId") or "")

    if raw_type in _TOOL_TYPES:
        return VoiceEvent(
            event_type=VoiceEventType.TOOL_CALLS,
            call_id=call_id,
            raw_type=raw_type,
            caller_phone=_caller_phone(call),
            tool_calls=_parse_tool_calls(message),
        )

    if raw_type in _ENDED_TYPES:
        return VoiceEvent(
            event_type=VoiceEventType.CALL_ENDED,
            call_id=call_id,
            raw_type=raw_type,
            caller_phone=_caller_phone(call),
            ended_reason=_as_optional_str(message.get("endedReason")),
            summary=_as_optional_str(message.get("summary")),
            transcript=_as_optional_str(message.get("transcript")),
        )

    if raw_type in _STARTED_TYPES or (
        raw_type == "status-update" and message.get("status") == "in-progress"
    ):
        return VoiceEvent(
            event_type=VoiceEventType.CALL_STARTED,
            call_id=call_id,
            raw_type=raw_type,
            caller_phone=_caller_phone(call),
        )

    if raw_type == "status-update" and message.get("status") == "ended":
        return VoiceEvent(
            event_type=VoiceEventType.CALL_ENDED,
            call_id=call_id,
            raw_type=raw_type,
            caller_phone=_caller_phone(call),
            ended_reason=_as_optional_str(message.get("endedReason")),
        )

    return VoiceEvent(
        event_type=VoiceEventType.IGNORED, call_id=call_id, raw_type=raw_type or "unknown"
    )


def _parse_tool_calls(message: dict[str, Any]) -> tuple[ToolInvocation, ...]:
    """Read tool calls out of whichever shape this Vapi version used."""
    invocations: list[ToolInvocation] = []

    # Current: message.toolCallList = [{id, name, arguments}]
    for entry in _as_list(message.get("toolCallList")):
        invocation = _tool_invocation(entry)
        if invocation is not None:
            invocations.append(invocation)

    # Also seen: message.toolCalls = [{id, function: {name, arguments}}]
    if not invocations:
        for entry in _as_list(message.get("toolCalls")):
            invocation = _tool_invocation(entry)
            if invocation is not None:
                invocations.append(invocation)

    # Legacy: message.functionCall = {name, parameters}
    if not invocations:
        legacy = message.get("functionCall")
        if isinstance(legacy, dict):
            name = _as_optional_str(legacy.get("name"))
            if name:
                invocations.append(
                    ToolInvocation(
                        invocation_id=str(legacy.get("id") or name),
                        name=name,
                        arguments=_parse_arguments(legacy.get("parameters")),
                    )
                )

    return tuple(invocations)


def _tool_invocation(entry: Any) -> ToolInvocation | None:
    if not isinstance(entry, dict):
        return None

    raw_function = entry.get("function")
    function: dict[str, Any] = raw_function if isinstance(raw_function, dict) else {}
    name = _as_optional_str(entry.get("name") or function.get("name"))
    if not name:
        return None

    raw_arguments = entry["arguments"] if "arguments" in entry else function.get("arguments")
    return ToolInvocation(
        invocation_id=str(entry.get("id") or entry.get("toolCallId") or name),
        name=name,
        arguments=_parse_arguments(raw_arguments),
    )


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """Arguments arrive as an object or as a JSON string, depending on the model."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            decoded = json.loads(raw)
        except ValueError:
            log.warning("voice.arguments_unparseable", extra=event(platform=PLATFORM_NAME))
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def format_tool_results(
    results: dict[str, str], *, transfer_to: str | None = None
) -> dict[str, Any]:
    """Build the response body Vapi expects for a `tool-calls` webhook.

    Maps invocation id -> the line the assistant should say. When `transfer_to`
    is set, a transfer destination is attached so Vapi hands the call over
    after speaking.
    """
    body: dict[str, Any] = {
        "results": [
            {"toolCallId": invocation_id, "result": result}
            for invocation_id, result in results.items()
        ]
    }

    if transfer_to:
        body["destination"] = transfer_destination(transfer_to)

    return body


def transfer_destination(number: str) -> dict[str, Any]:
    """Vapi's transfer-destination object for a phone number.

    The only place in the codebase that knows what a transfer looks like on the
    wire. `EscalationService` deals in "is a transfer available", and the tool
    returns a provider-neutral `transfer_to`; this turns that into Vapi's shape.

    Requires a transfer destination configured on the assistant. Unset in this
    build, so the realistic escalation workflow is what actually runs — see
    docs/DEFERRED.md item 5.2.
    """
    return {
        "type": "number",
        "number": number,
        "message": "I'm connecting you to a representative now.",
    }


def supports_transfer(configured_number: str | None) -> bool:
    """Whether this deployment can actually hand a call over.

    Kept here rather than in the escalation service so that "can we transfer"
    stays a question about the platform, not about business logic.
    """
    return bool(configured_number and configured_number.strip())


def acknowledgement() -> dict[str, Any]:
    """The body for an event that needs no answer. Vapi accepts an empty object."""
    return {}


def _caller_phone(call: dict[str, Any]) -> str | None:
    """The caller's number, when the platform knows it.

    Treated as a hint that seeds the lookup, never as proof of identity — the
    caller still verifies.
    """
    raw_customer = call.get("customer")
    customer: dict[str, Any] = raw_customer if isinstance(raw_customer, dict) else {}
    return _as_optional_str(customer.get("number"))


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_optional_str(value: Any) -> str | None:
    return str(value) if isinstance(value, (str, int, float)) and str(value) else None


# --- assistant configuration --------------------------------------------------


def tool_schemas(definitions: list[ToolDefinition]) -> list[dict[str, Any]]:
    """Render our tool definitions in Vapi's assistant-configuration format.

    Emitted by `GET /api/v1/voice/assistant-config` so the Vapi assistant can be
    configured from the code that actually implements the tools, rather than by
    hand-copying schemas that then drift.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": definition.name,
                "description": definition.description,
                "parameters": definition.json_schema(),
            },
            "server": {"url": "<your public base URL>/api/v1/voice/webhook"},
            "async": False,
        }
        for definition in definitions
    ]
