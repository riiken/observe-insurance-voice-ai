"""Turning structured claim data into something worth hearing.

A voice line is not a serialised object read aloud. This module exists so that
"how it sounds" is a testable function of the data rather than a hope about what
the model will do with a JSON blob (CLAUDE.md §16).

The rules it follows:

- Short. A caller cannot skim, and cannot re-read a sentence they missed.
- One question at a time, asked last, so it is the part still in mind.
- No markdown, no identifiers spelled out unless they are useful, no jargon.
- Never a promise. No approval, no payment timing, no "should be soon" —
  claim outcomes are not ours to forecast on a phone call.

Every sentence about *what happens next* comes from configured guidance. This
module chooses which configured line to speak and how to join it to the facts;
it never composes advice of its own.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from app.models.claim import Claim
from app.models.enums import ClaimStatus
from app.services.guidance import ClaimGuidance

# Configured entries that already carry a determiner need no article added.
_DETERMINERS = frozenset({"a", "an", "the", "your", "any", "all", "some", "two", "three"})


def render_claim_status(claim: Claim, guidance: ClaimGuidance) -> str:
    """Speak a claim's status, when it changed, and what happens next."""
    status_guidance = guidance.for_status(claim.status)

    sentences = [f"{_capitalise(status_guidance.summary)}."]

    if claim.last_updated is not None:
        sentences.append(f"It was last updated on {speak_date(claim.last_updated)}.")

    if claim.status is ClaimStatus.DOCUMENTS_REQUIRED:
        sentences.extend(_document_sentences(claim, guidance))
    else:
        sentences.append(status_guidance.speech_next_step)

    return " ".join(sentences)


def _document_sentences(claim: Claim, guidance: ClaimGuidance) -> list[str]:
    documents = list(claim.required_documents)

    if not documents:
        # The sheet says documents are needed but does not say which. Inventing
        # a plausible list here would be worse than admitting the gap.
        return [
            "I can see some documents are needed, but I don't have the list in "
            "front of me. Let me put you through to a representative who can "
            "tell you exactly what's outstanding."
        ]

    return [
        f"We need {speak_list(_with_article(document) for document in documents)}.",
        guidance.for_status(claim.status).speech_next_step,
        "Would you like me to explain how to send those in?",
    ]


def speak_list(items: Iterable[str]) -> str:
    """Join items the way a person would: 'a, b and c'."""
    values = [item for item in items if item]

    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    return f"{', '.join(values[:-1])} and {values[-1]}"


def speak_date(value: date) -> str:
    """'2026-08-28' -> 'August the 28th'.

    Spelled out because a text-to-speech engine handed '2026-08-28' will read
    it as digits, and a caller hearing "twenty twenty six dash zero eight" has
    learned nothing.
    """
    return f"{value.strftime('%B')} the {_ordinal(value.day)}"


def speak_reference(reference: str) -> str:
    """Space out an identifier so it is heard correctly.

    'CLM-88402' spoken as one token comes out as a word; broken up, the caller
    can write it down.
    """
    return reference.replace("-", " ").strip()


def _speak_email(email: str) -> str:
    """'documents@observeinsurance.com' -> 'documents at observeinsurance.com'."""
    return email.replace("@", " at ")


def _ordinal(day: int) -> str:
    if 11 <= day % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def _capitalise(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _with_article(document: str) -> str:
    """'Police report' -> 'a police report', so the list reads as a sentence.

    A configured entry that already opens with a determiner, or that is plural,
    is left alone: "we need a photographs" is worse than no article at all.
    """
    text = _lowercase_first(document.strip())
    if not text:
        return text

    first_word = text.split(" ", 1)[0]
    if first_word in _DETERMINERS or _looks_plural(first_word):
        return text

    article = "an" if text[0] in "aeiou" else "a"
    return f"{article} {text}"


def _looks_plural(word: str) -> bool:
    """Rough enough for document names: 'photographs' yes, 'loss' no."""
    return word.endswith("s") and not word.endswith(("ss", "us", "is"))


def _lowercase_first(text: str) -> str:
    """'Police report' -> 'police report', so it reads inside a sentence."""
    return text[:1].lower() + text[1:] if text else text


__all__ = [
    "render_claim_status",
    "speak_date",
    "speak_list",
    "speak_reference",
]
