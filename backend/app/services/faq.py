"""FAQ answering.

Matching is deterministic keyword overlap, not a language model. That is the
whole point: an answer either exists in `knowledge/faq.json` and is read out
verbatim, or no answer exists and the caller is offered a representative. There
is no middle path where something plausible gets composed, which is what
CLAUDE.md §12 and §15 rule out.

The scoring is deliberately unclever. A confident wrong answer about an
insurance claim is worse than an honest "let me put you through to someone",
so the threshold errs towards handing off.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.core.errors import AppError
from app.core.logging import event, get_logger

log = get_logger(__name__)

# backend/app/services/faq.py -> repository root
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FAQ_PATH = _REPO_ROOT / "knowledge" / "faq.json"

# Words that match everything and therefore distinguish nothing.
_STOP_WORDS = frozenset(
    """a an and are as at be by can could do does for from get got had has have
    how i if in is it its me my of on or please que should so tell that the
    their them there they this to us was we were what when where which who why
    will with would you your""".split()
)

_TOKEN = re.compile(r"[a-z0-9']+")


class FaqOutcome(StrEnum):
    ANSWER_FOUND = "ANSWER_FOUND"
    NO_ANSWER = "NO_ANSWER"


class FaqConfigurationError(AppError):
    code = "FAQ_CONFIGURATION_ERROR"
    message = "FAQ content is not configured correctly."


class FaqEntry(BaseModel):
    id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    keywords: list[str] = Field(min_length=1)
    answer: str = Field(min_length=1)


class FaqContent(BaseModel):
    # Tuned so a question that shares one incidental word does not match. Raise
    # it to hand off more readily; lower it only with evidence.
    minimum_score: float = Field(default=0.34, gt=0, le=1)
    entries: list[FaqEntry] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class FaqResult:
    """An answer the agent may read out, or an honest absence of one."""

    outcome: FaqOutcome
    entry: FaqEntry | None = None
    score: float = 0.0

    @property
    def is_answered(self) -> bool:
        return self.outcome is FaqOutcome.ANSWER_FOUND


class FaqService:
    """Answers the four supported topics, and nothing else."""

    def __init__(self, content: FaqContent) -> None:
        self._content = content
        self._keywords = {
            entry.id: frozenset(word.lower() for word in entry.keywords)
            for entry in content.entries
        }

    @property
    def topics(self) -> list[str]:
        """What the agent can truthfully offer to help with."""
        return [entry.topic for entry in self._content.entries]

    def search(self, question: str) -> FaqResult:
        """Find the configured answer for a question, if there is one."""
        tokens = _tokenise(question)
        if not tokens:
            return FaqResult(FaqOutcome.NO_ANSWER)

        best: FaqEntry | None = None
        best_score = 0.0

        for entry in self._content.entries:
            score = self._score(tokens, entry)
            if score > best_score:
                best, best_score = entry, score

        if best is None or best_score < self._content.minimum_score:
            log.info(
                "faq.lookup",
                extra=event(outcome="NO_ANSWER", score=round(best_score, 3)),
            )
            return FaqResult(FaqOutcome.NO_ANSWER, score=best_score)

        log.info(
            "faq.lookup",
            extra=event(outcome="ANSWER_FOUND", entry=best.id, score=round(best_score, 3)),
        )
        return FaqResult(FaqOutcome.ANSWER_FOUND, entry=best, score=best_score)

    def _score(self, tokens: frozenset[str], entry: FaqEntry) -> float:
        """Fraction of the caller's meaningful words this entry accounts for.

        Scoring against the *question* rather than the keyword list stops a
        long keyword list from winning every time.
        """
        matched = tokens & self._keywords[entry.id]
        return len(matched) / len(tokens)


def _tokenise(text: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(text.lower())) - _STOP_WORDS


def load_faq_content(path: Path | None = None) -> FaqContent:
    """Read and validate the FAQ file. Failures are loud and happen at startup."""
    source = path or DEFAULT_FAQ_PATH

    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FaqConfigurationError("FAQ file not found.", path=str(source)) from exc
    except (OSError, ValueError) as exc:
        raise FaqConfigurationError("FAQ file could not be read.", path=str(source)) from exc

    if isinstance(raw, dict):
        raw.pop("_comment", None)

    try:
        content = FaqContent.model_validate(raw)
    except ValidationError as exc:
        raise FaqConfigurationError(
            "FAQ file is not valid.", path=str(source), errors=exc.error_count()
        ) from exc

    identifiers = [entry.id for entry in content.entries]
    if len(set(identifiers)) != len(identifiers):
        raise FaqConfigurationError("FAQ entry ids must be unique.", path=str(source))

    log.info("faq.loaded", extra=event(path=str(source), entries=len(content.entries)))
    return content
