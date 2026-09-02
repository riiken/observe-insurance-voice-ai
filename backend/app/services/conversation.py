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

from app.core.context import reset_call_id, set_call_id
from app.core.logging import event, get_logger
from app.integrations.voice_platform import VoiceEvent, VoiceEventType
from app.models.enums import AuthenticationStatus, ConversationOutcome
from app.services.authentication import AuthenticationService
from app.services.session_store import SessionStore
from app.tools.registry import ToolRegistry

log = get_logger(__name__)


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
    ) -> None:
        self._authentication = authentication
        self._sessions = sessions
        self._tools = tools

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
            await self._authentication.start_call(
                voice_event.call_id, caller_phone=voice_event.caller_phone
            )
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
                "tool.invoked",
                extra=event(tool=invocation.name, arguments=sorted(invocation.arguments)),
            )
            # call_id comes from the platform payload, never from the model.
            result = await self._tools.invoke(
                invocation.name, voice_event.call_id, invocation.arguments
            )
            results[invocation.invocation_id] = result.speech
            transfer_to = transfer_to or result.transfer_to

            log.info(
                "tool.completed",
                extra=event(tool=invocation.name, outcome=result.outcome),
            )

        return results, transfer_to

    async def _complete(self, voice_event: VoiceEvent) -> None:
        """Finalise the call and release the session.

        The outcome is derived from session state, not from the model's summary:
        whether a caller was authenticated or escalated is something we know,
        and it should not depend on how a transcript was worded. Persisting an
        interaction record is Integration #2 (see docs/DEFERRED.md).
        """
        session = await self._sessions.get(voice_event.call_id)
        if session is None:
            log.info("call.completed", extra=event(outcome="UNKNOWN_SESSION"))
            return

        outcome = _derive_outcome(session.authentication_status, session.escalated)
        session = await self._authentication.complete(voice_event.call_id, outcome)

        log.info(
            "call.completed",
            extra=event(
                **session.log_fields(),
                outcome=outcome,
                ended_reason=voice_event.ended_reason,
                has_summary=voice_event.summary is not None,
            ),
        )

        # Freeing the session here is what keeps the in-memory store bounded.
        await self._sessions.discard(voice_event.call_id)


def _derive_outcome(status: AuthenticationStatus, escalated: bool) -> ConversationOutcome:
    """How the call ended, from what we observed rather than what was said."""
    if escalated:
        return ConversationOutcome.ESCALATED
    if status is AuthenticationStatus.AUTHENTICATED:
        return ConversationOutcome.RESOLVED
    if status is AuthenticationStatus.AUTHENTICATION_FAILED:
        return ConversationOutcome.AUTHENTICATION_FAILED
    return ConversationOutcome.ABANDONED
