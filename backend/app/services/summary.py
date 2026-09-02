"""Summarising a call, and scoring how it went.

Both are derived from **state we observed**, not from a transcript.

That is the whole design. CLAUDE.md §17 requires a summary of the actual
interaction and forbids inventing events; a language model reading a transcript
can do neither reliably, and would make every post-call record
non-deterministic and untestable. The voice platform sends its own model-written
summary on `end-of-call-report`; it is deliberately not used, because we cannot
tell whether it describes something that happened.

The cost is honest: these summaries are plainer than a model would write, and
sentiment here means "how did this call go for the caller", inferred from
outcome rather than from tone of voice. Both are stated as such in the README so
nobody reads more into a row than is there.
"""

from __future__ import annotations

from app.models.enums import (
    AuthenticationStatus,
    ConversationOutcome,
    EscalationReason,
    Sentiment,
)
from app.models.session import SessionState

ANONYMOUS_CALLER = "Unknown caller"


def summarise(session: SessionState) -> str:
    """One or two plain sentences describing what actually happened."""
    parts: list[str] = [_identification_sentence(session)]

    if topic_sentence := _topic_sentence(session):
        parts.append(topic_sentence)

    if claim_sentence := _claim_sentence(session):
        parts.append(claim_sentence)

    parts.append(_closing_sentence(session))

    return " ".join(part for part in parts if part)


def _identification_sentence(session: SessionState) -> str:
    status = session.authentication_status

    if status is AuthenticationStatus.AUTHENTICATED:
        return f"Caller verified as {session.customer_name}."
    if status is AuthenticationStatus.AUTHENTICATION_FAILED:
        return f"Caller could not be verified after {session.authentication_attempts} attempts."
    if session.conversation_outcome is ConversationOutcome.CUSTOMER_NOT_FOUND:
        return "No account was found for the number the caller gave."
    if status is AuthenticationStatus.CUSTOMER_FOUND:
        return "Account identified; the caller did not complete verification."
    return "Caller was not identified."


def _topic_sentence(session: SessionState) -> str:
    if not session.faq_topics:
        return ""
    topics = ", ".join(topic.lower() for topic in session.faq_topics)
    return f"Asked about {topics}."


def _claim_sentence(session: SessionState) -> str:
    if session.claim_id is None:
        return ""
    return f"Claim {session.claim_id} was discussed."


def _closing_sentence(session: SessionState) -> str:
    if session.escalated:
        reason = session.escalation_reason or EscalationReason.CALLER_REQUEST
        if reason == EscalationReason.EMERGENCY:
            return "An emergency was reported and the call was escalated immediately."
        return f"Escalated to a representative ({_humanise(reason)})."

    outcome = session.conversation_outcome
    if outcome is ConversationOutcome.RESOLVED:
        return "Call completed."
    if outcome is ConversationOutcome.AUTHENTICATION_FAILED:
        return "Call ended without verification."
    if outcome is ConversationOutcome.CUSTOMER_NOT_FOUND:
        return "Call ended without an account match."
    return "Call ended."


def score_sentiment(session: SessionState) -> Sentiment:
    """How the call went *for the caller*, from what we observed.

    Not tone analysis — we never see the audio, and a transcript-based judgement
    would be a guess dressed as a measurement. This reads outcomes, which is
    less rich and considerably more defensible.
    """
    reason = session.escalation_reason

    # An emergency, or a caller locked out of their own account, is a bad call
    # whatever else happened.
    if reason == EscalationReason.EMERGENCY:
        return Sentiment.NEGATIVE
    if session.authentication_status is AuthenticationStatus.AUTHENTICATION_FAILED:
        return Sentiment.NEGATIVE
    if session.conversation_outcome is ConversationOutcome.CUSTOMER_NOT_FOUND:
        return Sentiment.NEGATIVE
    if session.escalated and reason in (
        EscalationReason.SYSTEM_ERROR,
        EscalationReason.CLAIM_DATA_INCOMPLETE,
    ):
        # We could not do the job; someone else has to.
        return Sentiment.NEGATIVE

    # A caller who got what they came for.
    if session.is_authenticated and session.claim_id and not session.escalated:
        return Sentiment.POSITIVE

    return Sentiment.NEUTRAL


def caller_name(session: SessionState) -> str:
    """The name to file the record under.

    Falls back to a placeholder rather than an empty cell: a row with no name is
    harder to read than one that says the caller was never identified.
    """
    return session.customer_name or ANONYMOUS_CALLER


def _humanise(value: str) -> str:
    return str(value).replace("_", " ").lower()
