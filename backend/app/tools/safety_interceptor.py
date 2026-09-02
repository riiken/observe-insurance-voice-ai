"""Emergency detection across every tool call.

An emergency does not arrive politely through `request_representative`. It
arrives when the agent asks for a date of birth and the caller says "please
help, my kitchen is on fire". So detection runs at the dispatcher, over whatever
the caller said, whichever tool was being called.

When it fires, the tool the agent asked for **does not run**. Looking up office
hours for someone whose house is burning is precisely the "unnecessary claims
troubleshooting" CLAUDE.md §14 forbids.

This is a safety net, not the only detector: the agent's instructions cover the
same ground. Two independent detectors, either sufficient.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import event, get_logger
from app.models.enums import EscalationReason
from app.services.escalation import EscalationService
from app.services.safety import SafetyService
from app.tools.base import ToolResult
from app.tools.representative_tool import build_result

log = get_logger(__name__)

# Arguments that carry the caller's own words. A phone number or a date of
# birth does not, and scanning them would only invite false positives.
_CALLER_SPEECH_ARGUMENTS = ("question", "notes")


class EmergencyInterceptor:
    """Stops a tool call and escalates when the caller describes an emergency."""

    def __init__(
        self,
        safety: SafetyService,
        escalation: EscalationService,
        *,
        transfer_to: str | None = None,
    ) -> None:
        self._safety = safety
        self._escalation = escalation
        self._transfer_to = transfer_to

    async def intercept(
        self, tool_name: str, call_id: str, arguments: dict[str, Any]
    ) -> ToolResult | None:
        """Return an emergency response, or None to let the tool proceed."""
        if tool_name == "request_representative":
            # That tool does its own assessment and would otherwise escalate
            # twice for one utterance.
            return None

        texts = [
            str(value)
            for name, value in arguments.items()
            if name in _CALLER_SPEECH_ARGUMENTS and isinstance(value, str)
        ]
        if not texts or not self._safety.assess(*texts).is_emergency:
            return None

        log.warning(
            "safety.tool_intercepted",
            extra=event(tool=tool_name, call_id=call_id),
        )
        record = await self._escalation.request_representative(
            call_id,
            EscalationReason.EMERGENCY,
            notes="Emergency detected in the caller's own words.",
        )
        return build_result(record, transfer_to=self._transfer_to)
