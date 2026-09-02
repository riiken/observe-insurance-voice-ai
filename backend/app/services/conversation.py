"""Handling voice-platform events.

Provider-neutral: this service takes a `VoiceEvent` and does not know or care
which platform produced it. It owns the call lifecycle — start, tool
invocations, completion — and nothing about HTTP or Vapi.

`call_id` propagation happens here. It is bound to the logging context for the
duration of the event, so every log line produced while handling a turn carries
the call it belongs to, and it is passed to tools by the dispatcher rather than
being a parameter the model can choose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.core import events
from app.core.context import reset_call_id, set_call_id
from app.core.logging import event, get_logger
from app.core.metrics import CALL_DURATION, CALLS_COMPLETED, CALLS_TOTAL, METRICS
from app.integrations.voice_platform import VoiceEvent, VoiceEventType
from app.models.enums import AuthenticationStatus, ConversationOutcome
from app.models.session import SessionState
from app.services.authentication import AuthenticationService
from app.services.postcall import PostCallService
from app.services.session_store import SessionStore
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry

log = get_logger(__name__)


@runtime_checkable
class Dispatcher(Protocol):
    """Something that can route a tool call and run it.

    Declared here rather than imported from `agents/` on purpose: the layering
    is services -> integrations -> core, and a service reaching *up* into the
    agent layer would invert it. The supervisor satisfies this structurally, so
    the orchestration layer depends on the service rather than the reverse.
    """

    async def dispatch(
        self, tool_name: str, call_id: str, arguments: dict[str, Any]
    ) -> tuple[ToolResult, Any]: ...


@dataclass(frozen=True, slots=True)
class ConversationResponse:
    """What the platform adapter should send back.

    `tool_results` maps invocation id -> the line to speak.
    """

    event_type: VoiceEventType
    tool_results: dict[str, str]
    # Provider-neutral: the adapter turns this into a platform transfer payload.
    transfer_to: str | None = None


class ConversationService:
    """Drives one call's lifecycle from platform events."""

    def __init__(
        self,
        *,
        authentication: AuthenticationService,
        sessions: SessionStore,
        tools: ToolRegistry,
        postcall: PostCallService,
        supervisor: Dispatcher | None = None,
    ) -> None:
        self._authentication = authentication
        self._sessions = sessions
        self._tools = tools
        self._postcall = postcall
        # Optional on purpose: without a supervisor the single-agent path runs
        # exactly as before, so the orchestration layer can be removed by
        # passing None rather than by unpicking it.
        self._supervisor = supervisor

    async def handle(self, voice_event: VoiceEvent) -> ConversationResponse:
        """Process one webhook event."""
        if not voice_event.call_id:
            log.warning("voice.event_without_call_id", extra=event(type=voice_event.raw_type))
            return ConversationResponse(VoiceEventType.IGNORED, {})

        # Bind the call id for every log line this event produces.
        token = set_call_id(voice_event.call_id)
        try:
            return await self._dispatch(voice_event)
        finally:
            reset_call_id(token)

    async def _dispatch(self, voice_event: VoiceEvent) -> ConversationResponse:
        if voice_event.event_type is VoiceEventType.CALL_STARTED:
            session = await self._sessions.get(voice_event.call_id)
            await self._authentication.start_call(
                voice_event.call_id, caller_phone=voice_event.caller_phone
            )
            if session is None:  # count a call once, not per redelivered event
                METRICS.increment(CALLS_TOTAL)
            return ConversationResponse(voice_event.event_type, {})

        if voice_event.event_type is VoiceEventType.TOOL_CALLS:
            results, transfer_to = await self._run_tools(voice_event)
            return ConversationResponse(voice_event.event_type, results, transfer_to)

        if voice_event.event_type is VoiceEventType.CALL_ENDED:
            await self._complete(voice_event)
            return ConversationResponse(voice_event.event_type, {})

        log.debug("voice.event_ignored", extra=event(type=voice_event.raw_type))
        return ConversationResponse(VoiceEventType.IGNORED, {})

    async def _run_tools(self, voice_event: VoiceEvent) -> tuple[dict[str, str], str | None]:
        """Run each requested tool and collect what to say.

        Tools run in order rather than concurrently: they share one session, and
        two turns mutating an attempt count at once is a race nobody wants in an
        authentication flow.
        """
        results: dict[str, str] = {}
        transfer_to: str | None = None

        for invocation in voice_event.tool_calls:
            log.info(
                events.TOOL_INVOKED,
                extra=event(tool=invocation.name, arguments=sorted(invocation.arguments)),
            )
            # call_id comes from the platform payload, never from the model.
            if self._supervisor is not None:
                result, routing = await self._supervisor.dispatch(
                    invocation.name, voice_event.call_id, invocation.arguments
                )
                specialist = routing.specialist.name
            else:
                result = await self._tools.invoke(
                    invocation.name, voice_event.call_id, invocation.arguments
                )
                specialist = None

            results[invocation.invocation_id] = result.speech
            transfer_to = transfer_to or result.transfer_to

            log.info(
                events.TOOL_COMPLETED,
                extra=event(
                    tool=invocation.name,
                    outcome=result.outcome,
                    specialist=specialist,
                ),
            )

        return results, transfer_to

    async def _complete(self, voice_event: VoiceEvent) -> None:
        """Finalise the call and release the session.

        The outcome is derived from session state, not from the model's summary:
        whether a caller was authenticated or escalated is something we know,
        and it should not depend on how a transcript was worded.
        """
        session = await self._sessions.get(voice_event.call_id)
        if session is None:
            log.info(events.CALL_COMPLETED, extra=event(outcome="UNKNOWN_SESSION"))
            return

        outcome = _derive_outcome(session)
        session = await self._authentication.complete(voice_event.call_id, outcome)

        duration_ms = (session.updated_at - session.started_at).total_seconds() * 1000
        METRICS.increment(CALLS_COMPLETED, outcome=str(outcome))
        METRICS.observe(CALL_DURATION, duration_ms)

        log.info(
            events.CALL_COMPLETED,
            extra=event(
                **session.log_fields(),
                outcome=outcome,
                duration_ms=round(duration_ms, 2),
                ended_reason=voice_event.ended_reason,
                has_summary=voice_event.summary is not None,
            ),
        )

        # Integration #2. `record_call` never raises, but the call is wrapped
        # anyway: nothing about filing paperwork may fail a webhook.
        try:
            await self._postcall.record_call(session)
        except Exception:  # pragma: no cover - defence in depth
            log.exception("postcall.failed", extra=event(call_id=voice_event.call_id))

        # Freeing the session here is what keeps the in-memory store bounded.
        await self._sessions.discard(voice_event.call_id)


def _derive_outcome(session: SessionState) -> ConversationOutcome:
    """How the call ended, from what we observed rather than what was said.

    An outcome already recorded during the call — "no account matched that
    number" — is more specific than anything derivable at the end, so it is
    kept rather than flattened to ABANDONED.
    """
    if session.escalated:
        return ConversationOutcome.ESCALATED

    status = session.authentication_status
    if status is AuthenticationStatus.AUTHENTICATED:
        return ConversationOutcome.RESOLVED
    if status is AuthenticationStatus.AUTHENTICATION_FAILED:
        return ConversationOutcome.AUTHENTICATION_FAILED
    if session.conversation_outcome is ConversationOutcome.CUSTOMER_NOT_FOUND:
        return ConversationOutcome.CUSTOMER_NOT_FOUND

    return ConversationOutcome.ABANDONED
