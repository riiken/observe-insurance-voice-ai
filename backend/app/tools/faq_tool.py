"""`search_faq`.

Returns a configured answer or an honest absence of one. There is no path here
that composes an answer, so "the agent should not hallucinate unsupported FAQ
answers" (CLAUDE.md §12) holds regardless of what the model would like to say.

Available before authentication: office hours and a mailing address are public
information, and making a caller verify to hear them would be theatre.
"""

from __future__ import annotations

from app.core.logging import event, get_logger
from app.services.faq import FaqService
from app.services.voice import speak_list
from app.tools.base import ToolOutcome, ToolResult

log = get_logger(__name__)


class SearchFaqTool:
    """Answers the supported general questions."""

    name = "search_faq"

    def __init__(self, faq: FaqService) -> None:
        self._faq = faq

    async def __call__(self, call_id: str, question: str) -> ToolResult:
        if not question or not question.strip():
            return self._no_answer(call_id, reason="EMPTY_QUESTION")

        result = self._faq.search(question)

        if not result.is_answered or result.entry is None:
            return self._no_answer(call_id, reason="NO_MATCH")

        return ToolResult(
            outcome=ToolOutcome.SUCCESS,
            speech=result.entry.answer,
            context={
                "call_id": call_id,
                "entry": result.entry.id,
                "score": f"{result.score:.2f}",
            },
        )

    def _no_answer(self, call_id: str, *, reason: str) -> ToolResult:
        """Say what we can help with, then offer a person. Never guess."""
        log.info("faq.lookup", extra=event(call_id=call_id, outcome="NO_ANSWER", reason=reason))
        topics = speak_list(topic.lower() for topic in self._faq.topics)
        return ToolResult(
            outcome=ToolOutcome.NOT_FOUND,
            speech=(
                "That's not something I can help with, I'm afraid. I can cover "
                f"{topics}. For anything else, I can put you through to a "
                "representative — would you like me to do that?"
            ),
            context={"call_id": call_id, "reason": reason},
        )
