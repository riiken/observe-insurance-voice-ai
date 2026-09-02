"""Shared shape for agent-callable tools.

A tool is the boundary between a conversation and the business logic. It
validates its input, enforces its authorization boundary, calls exactly one
service, and returns a structured result the agent can act on — including for
failures, which are returned rather than raised so a broken upstream ends a
sentence rather than a call.

Every tool result carries two things: `data` for the agent to reason with, and
`speech` for it to say. Keeping the spoken line here rather than leaving the
model to compose one from `data` is what makes voice output testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel

TPayload = TypeVar("TPayload", bound=BaseModel)


class ToolOutcome(StrEnum):
    """How a tool call ended.

    The agent branches on this, never on the wording of `speech`.
    """

    SUCCESS = "SUCCESS"
    NOT_FOUND = "NOT_FOUND"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    INCOMPLETE_DATA = "INCOMPLETE_DATA"
    INTEGRATION_ERROR = "INTEGRATION_ERROR"
    # The caller gave something we could not use — a mis-heard phone number,
    # an empty answer. Distinct from NOT_FOUND: there is something to retry.
    INVALID_INPUT = "INVALID_INPUT"
    # A bounded budget is spent. Retrying cannot help; a human can.
    EXHAUSTED = "EXHAUSTED"


@dataclass(frozen=True, slots=True)
class ToolResult(Generic[TPayload]):
    """A tool's structured answer plus the line to speak.

    `data` is present only on SUCCESS. On every other outcome it is None — so a
    failed call has nothing in it to read out by mistake, and no partially
    populated object for the agent to mine.
    """

    outcome: ToolOutcome
    speech: str
    data: TPayload | None = None
    # Safe-to-log detail. Never spoken, never returned to a caller.
    context: dict[str, str] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.outcome is ToolOutcome.SUCCESS

    @property
    def should_offer_representative(self) -> bool:
        """Failures a caller cannot resolve by talking to us are for a human."""
        return self.outcome in (
            ToolOutcome.INCOMPLETE_DATA,
            ToolOutcome.INTEGRATION_ERROR,
            ToolOutcome.EXHAUSTED,
        )
