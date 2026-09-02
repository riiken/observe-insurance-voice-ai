"""The set of tools the agent may call, and their contracts.

Provider-neutral on purpose. `integrations/voice_platform.py` translates these
definitions into whatever shape the platform wants; nothing here knows Vapi
exists.

Two properties matter more than the plumbing:

**This list is the whole attack surface.** The agent can call these five things
and nothing else. There is no generic query tool, no "call this API", no
escape hatch — so the worst a compromised prompt can do is call a narrow
operation that enforces its own rules.

**`call_id` is never a parameter.** It is supplied by the platform's webhook
payload and injected by the dispatcher, so the model cannot name a different
call and inherit its authentication. Every parameter schema below describes
only what the *caller* said.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.core.logging import event, get_logger
from app.tools.base import ToolOutcome, ToolResult

log = get_logger(__name__)

ToolHandler = Callable[..., Awaitable[ToolResult]]


@dataclass(frozen=True, slots=True)
class ToolParameter:
    name: str
    description: str
    required: bool = True
    json_type: str = "string"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """One callable operation, with the description the model sees."""

    name: str
    description: str
    parameters: tuple[ToolParameter, ...]
    handler: ToolHandler

    def json_schema(self) -> dict[str, Any]:
        """Provider-neutral JSON Schema for this tool's arguments."""
        return {
            "type": "object",
            "properties": {
                parameter.name: {
                    "type": parameter.json_type,
                    "description": parameter.description,
                }
                for parameter in self.parameters
            },
            "required": [p.name for p in self.parameters if p.required],
        }


class ToolRegistry:
    """Dispatches a tool call by name, with `call_id` supplied by the platform."""

    def __init__(self, definitions: list[ToolDefinition]) -> None:
        self._definitions = {definition.name: definition for definition in definitions}

    @property
    def definitions(self) -> list[ToolDefinition]:
        return list(self._definitions.values())

    @property
    def names(self) -> set[str]:
        return set(self._definitions)

    async def invoke(self, name: str, call_id: str, arguments: dict[str, Any]) -> ToolResult:
        """Run a tool. Unknown names and bad arguments fail safe, never loudly.

        A tool that blows up must not take the call with it, so every failure
        becomes a speakable result. Nothing here can authorise anything: the
        registry only routes.
        """
        definition = self._definitions.get(name)
        if definition is None:
            log.warning("tool.unknown", extra=event(tool=name, call_id=call_id))
            return _unavailable(call_id, reason="UNKNOWN_TOOL")

        accepted = {parameter.name for parameter in definition.parameters}
        # Anything the model invented is dropped rather than forwarded. This is
        # what stops a stray `authenticated=true` from reaching a handler.
        supplied = {key: value for key, value in (arguments or {}).items() if key in accepted}
        ignored = sorted(set(arguments or {}) - accepted)
        if ignored:
            log.warning(
                "tool.arguments_ignored",
                extra=event(tool=name, call_id=call_id, ignored=ignored),
            )

        missing = [
            parameter.name
            for parameter in definition.parameters
            if parameter.required and not supplied.get(parameter.name)
        ]
        if missing:
            log.warning(
                "tool.arguments_missing",
                extra=event(tool=name, call_id=call_id, missing=missing),
            )
            return _unavailable(call_id, reason="MISSING_ARGUMENTS")

        try:
            return await definition.handler(call_id=call_id, **supplied)
        except Exception:
            # A tool that raises is a bug on our side; the caller hears an
            # apology rather than silence, and the traceback goes to the log.
            log.exception("tool.error", extra=event(tool=name, call_id=call_id))
            return _unavailable(call_id, reason="TOOL_FAILED")


def _unavailable(call_id: str, *, reason: str) -> ToolResult:
    return ToolResult(
        outcome=ToolOutcome.INTEGRATION_ERROR,
        speech=(
            "Sorry, I can't do that just now. Let me put you through to a "
            "representative who can help."
        ),
        context={"call_id": call_id, "reason": reason},
    )


def build_registry(
    *,
    lookup_customer: ToolHandler,
    verify_identity: ToolHandler,
    get_claim_status: ToolHandler,
    search_faq: ToolHandler,
    request_representative: ToolHandler,
) -> ToolRegistry:
    """The five tools the agent is given. Adding a sixth is a deliberate act."""
    return ToolRegistry(
        [
            ToolDefinition(
                name="lookup_customer",
                description=(
                    "Look up the caller's account from the phone number they just "
                    "gave you. Call this once you have heard a phone number. Does "
                    "not disclose any claim information."
                ),
                parameters=(
                    ToolParameter(
                        name="phone_number",
                        description=(
                            "The phone number exactly as the caller said it, "
                            "digits only or spoken form. Do not guess or reformat."
                        ),
                    ),
                ),
                handler=lookup_customer,
            ),
            ToolDefinition(
                name="verify_identity",
                description=(
                    "Check the verification value the caller gave, normally their "
                    "date of birth. This is the only way a caller can become "
                    "verified. Call it after lookup_customer has found an account."
                ),
                parameters=(
                    ToolParameter(
                        name="verification_value",
                        description=(
                            "What the caller said, verbatim. A date of birth is "
                            "expected as YYYY-MM-DD."
                        ),
                    ),
                ),
                handler=verify_identity,
            ),
            ToolDefinition(
                name="get_claim_status",
                description=(
                    "Get the caller's claim status, required documents and next "
                    "step. Only works once verify_identity has succeeded; it will "
                    "refuse otherwise. Never describe a claim without calling this."
                ),
                parameters=(),
                handler=get_claim_status,
            ),
            ToolDefinition(
                name="search_faq",
                description=(
                    "Answer a general question about office hours, the mailing "
                    "address, starting a claim, how the claims process works, or "
                    "sending documents in. Use this instead of answering from "
                    "memory. Works before verification."
                ),
                parameters=(
                    ToolParameter(
                        name="question",
                        description="The caller's question, in their own words.",
                    ),
                ),
                handler=search_faq,
            ),
            ToolDefinition(
                name="request_representative",
                description=(
                    "Transfer the caller to a human. Use whenever they ask for a "
                    "person, when you cannot help, or immediately if anyone is "
                    "hurt or in danger. Works at any point, verified or not."
                ),
                parameters=(
                    ToolParameter(
                        name="reason",
                        description=(
                            "One of: CALLER_REQUEST, AUTHENTICATION_FAILED, "
                            "CUSTOMER_NOT_FOUND, UNSUPPORTED_REQUEST, "
                            "CLAIM_DATA_INCOMPLETE, SYSTEM_ERROR, EMERGENCY."
                        ),
                        required=False,
                    ),
                    ToolParameter(
                        name="notes",
                        description="One short line on what the caller wanted.",
                        required=False,
                    ),
                ),
                handler=request_representative,
            ),
        ]
    )
