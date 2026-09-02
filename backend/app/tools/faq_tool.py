"""`search_faq`.

Returns a configured answer with a confidence indicator, or an honest absence of
one. There is no path here that composes an answer, so "the agent should not
hallucinate unsupported FAQ answers" (CLAUDE.md §12) holds regardless of what
the model would like to say.

Available before authentication: office hours and a mailing address are public
information, and making a caller verify to hear them would be theatre.
"""

from __future__ import annotations

from app.core.logging import event, get_logger
from app.schemas.faq import FaqAnswerView
from app.services.faq import Confidence, FaqOutcome, FaqService
from app.services.session_store import SessionStore
from app.services.voice import speak_list
from app.tools.base import ToolOutcome, ToolResult

log = get_logger(__name__)

SearchFaqToolResult = ToolResult[FaqAnswerView]

# Spoken when a match is real but thin. The answer is still the configured one —
# only the framing changes, so the caller can tell us we have missed the point.
_HEDGE_PREFIX = "I think this is what you're after. "


class SearchFaqTool:
    """Answers the supported general questions."""

    name = "search_faq"

    def __init__(self, faq: FaqService, sessions: SessionStore | None = None) -> None:
        self._faq = faq
        # Optional: used only to note what the call was about, so the post-call
        # summary can say so without inferring it from a transcript.
        self._sessions = sessions

    async def __call__(self, call_id: str, question: str) -> SearchFaqToolResult:
        if not question or not question.strip():
            return self._no_answer(call_id, reason="EMPTY_QUESTION")

        try:
            result = self._faq.search(question)
        except Exception:
            # Retrieval itself failed. Say nothing about the topic rather than
            # falling back to whatever the model remembers.
            log.exception("tool.error", extra=event(operation=self.name, call_id=call_id))
            return self._retrieval_failed(call_id)

        if not result.is_answered or result.entry is None:
            reason = "LOW_CONFIDENCE" if result.outcome is FaqOutcome.LOW_CONFIDENCE else "NO_MATCH"
            return self._no_answer(call_id, reason=reason, score=result.score)

        entry = result.entry
        view = FaqAnswerView(
            topic=entry.topic,
            answer=entry.answer,
            confidence=result.confidence,
            relevance_score=round(result.score, 3),
            source=entry.source,
            matched_terms=list(result.matched_terms),
            is_demo_content=entry.is_demo_content,
        )

        await self._note_topic(call_id, entry.topic)

        speech = entry.answer
        if result.confidence is Confidence.MEDIUM:
            speech = f"{_HEDGE_PREFIX}{speech}"

        return ToolResult(
            outcome=ToolOutcome.SUCCESS,
            speech=speech,
            data=view,
            context={
                "call_id": call_id,
                "entry": entry.id,
                "source": entry.source,
                "confidence": str(result.confidence),
                "score": f"{result.score:.2f}",
            },
        )

    async def _note_topic(self, call_id: str, topic: str) -> None:
        """Record an answered topic on the session, best-effort."""
        if self._sessions is None or not call_id:
            return
        session = await self._sessions.get(call_id)
        if session is not None:
            await self._sessions.save(session.with_faq_topic(topic))

    def _no_answer(self, call_id: str, *, reason: str, score: float = 0.0) -> SearchFaqToolResult:
        """Say what we can help with, then offer a person. Never guess.

        `data` stays None, so there is no half-populated answer for the agent to
        read out and no topic name to build a plausible reply around.
        """
        log.info(
            "faq.lookup",
            extra=event(call_id=call_id, outcome="NO_ANSWER", reason=reason, score=round(score, 3)),
        )
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

    @staticmethod
    def _retrieval_failed(call_id: str) -> SearchFaqToolResult:
        return ToolResult(
            outcome=ToolOutcome.INTEGRATION_ERROR,
            speech=(
                "Sorry, I can't look that up at the moment. Let me put you "
                "through to a representative who can help."
            ),
            context={"call_id": call_id, "reason": "RETRIEVAL_FAILED"},
        )
