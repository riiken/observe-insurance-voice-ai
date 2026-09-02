"""Multi-agent orchestration: a supervisor and three specialists.

    Supervisor
    ├── Claims Specialist      identification and claim questions
    ├── FAQ Specialist         the supported general questions
    └── Escalation Handler     representative requests and emergencies

## What the specialists are, and are not

Each specialist owns a *domain* — which tools belong to it, and which outcomes
in that domain are worth counting. **None of them owns business logic.** A
specialist delegates to the same `ToolRegistry` the single-agent path used, so
there is exactly one implementation of authentication, one of claim access, one
of FAQ retrieval. Duplicating any of that across agents is how multi-agent
systems start disagreeing with themselves.

## Why routing is deterministic and not a second model call

The obvious implementation is a supervisor LLM that reads the caller's words and
picks a specialist. It is the wrong one here:

- **Latency.** A caller is on the phone. A routing call would add a second model
  round trip to every turn, on a path where the whole tool budget is six
  seconds.
- **A second opinion that can disagree.** The assistant has *already* expressed
  the caller's intent by choosing a tool. Asking a different model to infer that
  intent again from the transcript produces a second answer which can differ —
  and then something has to arbitrate.
- **Determinism.** This system's argument is that its behaviour is a property of
  code rather than of model output. Putting a model in the routing path would
  make "which specialist handled this turn" unreproducible, and untestable.

So the supervisor derives intent from the tool the assistant chose plus the
session state. That is not a weaker signal than a transcript — it is the same
intent, already resolved, without the extra hop.

## What this layer cannot do

**It cannot authenticate anyone.** No specialist holds a session store, and none
can mutate session state. The supervisor routes and observes; the authorization
boundary stays exactly where it was, in `require_authenticated`, checked inside
the service. That is the point: adding agents must not move the boundary, and
the tests assert it has not.

**It cannot refuse a tool.** An unrecognised tool falls through to the registry,
which already handles unknown names safely. A routing layer that could veto
would be a second place for a call to fail, and the brief is explicit that
complexity which reduces reliability should not survive.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.logging import event, get_logger
from app.core.metrics import METRICS
from app.models.session import SessionState
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry

log = get_logger(__name__)

AGENT_ROUTED = "agent_routed_total"
AGENT_ROUTED_EVENT = "agent.routed"


class Intent(StrEnum):
    """What the caller is trying to do on this turn.

    Derived from the tool the assistant chose, which is the assistant's own
    resolution of the caller's intent — not a second guess at it.
    """

    IDENTIFY = "IDENTIFY"
    CLAIM = "CLAIM"
    FAQ = "FAQ"
    ESCALATION = "ESCALATION"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Specialist:
    """One domain: the tools it owns and the intent it serves.

    Deliberately data, not behaviour. Everything a specialist would "do" is
    already implemented once in the services its tools call, so giving it
    methods would only create somewhere for a second implementation to grow.
    """

    name: str
    intent: Intent
    tools: frozenset[str]

    def owns(self, tool_name: str) -> bool:
        return tool_name in self.tools


CLAIMS_SPECIALIST = Specialist(
    name="claims_specialist",
    intent=Intent.CLAIM,
    # Identification belongs here: looking a caller up and verifying them exist
    # only to reach a claim, and splitting them into a fourth agent would add a
    # handoff without adding a decision.
    tools=frozenset({"lookup_customer", "verify_identity", "get_claim_status"}),
)

FAQ_SPECIALIST = Specialist(
    name="faq_specialist",
    intent=Intent.FAQ,
    tools=frozenset({"search_faq"}),
)

ESCALATION_HANDLER = Specialist(
    name="escalation_handler",
    intent=Intent.ESCALATION,
    tools=frozenset({"request_representative"}),
)

SPECIALISTS: tuple[Specialist, ...] = (
    CLAIMS_SPECIALIST,
    FAQ_SPECIALIST,
    ESCALATION_HANDLER,
)

# Used when no specialist owns the tool. Routing still happens so the turn is
# observable; dispatch falls through to the registry unchanged.
UNROUTED = Specialist(name="unrouted", intent=Intent.UNKNOWN, tools=frozenset())


@dataclass(frozen=True, slots=True)
class Routing:
    """Which specialist handled a turn, and why."""

    specialist: Specialist
    intent: Intent
    tool: str

    @property
    def is_routed(self) -> bool:
        return self.specialist is not UNROUTED


class Supervisor:
    """Routes a turn to a specialist, then dispatches it unchanged.

    Holds no session store and no service. It cannot read or write conversation
    state, which is what makes "the supervisor cannot bypass authentication"
    true by construction rather than by discipline.
    """

    def __init__(
        self, tools: ToolRegistry, specialists: tuple[Specialist, ...] = SPECIALISTS
    ) -> None:
        self._tools = tools
        self._specialists = specialists
        self._by_tool = {
            tool: specialist for specialist in specialists for tool in specialist.tools
        }

    @property
    def specialists(self) -> tuple[Specialist, ...]:
        return self._specialists

    def route(self, tool_name: str) -> Routing:
        """Pick the specialist that owns this tool."""
        specialist = self._by_tool.get(tool_name, UNROUTED)
        return Routing(specialist=specialist, intent=specialist.intent, tool=tool_name)

    async def dispatch(
        self, tool_name: str, call_id: str, arguments: dict[str, Any]
    ) -> tuple[ToolResult, Routing]:
        """Route the turn and run it.

        The result is whatever the registry produced — the supervisor adds
        observability, never behaviour. An unrouted tool runs exactly as it did
        before this layer existed.
        """
        routing = self.route(tool_name)

        METRICS.increment(AGENT_ROUTED, specialist=routing.specialist.name)
        log.info(
            AGENT_ROUTED_EVENT,
            extra=event(
                call_id=call_id,
                specialist=routing.specialist.name,
                intent=routing.intent,
                tool=tool_name,
            ),
        )

        result = await self._tools.invoke(tool_name, call_id, arguments)
        return result, routing

    @staticmethod
    def describe(session: SessionState) -> Intent:
        """The intent the session is *currently in the middle of*.

        Reported for observability — "where was this caller when it went wrong"
        — and never used to decide what a tool is allowed to do.
        """
        if session.escalated:
            return Intent.ESCALATION
        if session.is_authenticated:
            return Intent.CLAIM
        if session.customer_id is not None:
            return Intent.IDENTIFY
        return Intent.UNKNOWN
