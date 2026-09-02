"""FAQ knowledge: loading, retrieval, and how sure we are.

Each supported topic is one Markdown file in `knowledge/`, with a frontmatter
block giving its id, topic and keywords, and a `## Answer` section that is read
aloud verbatim. Everything else in the file is maintainer notes and is never
spoken.

**Retrieval is deterministic keyword overlap, not a language model and not
embeddings.** For five documents that is the right tool: it is exact, it is
instant, it needs no dependency or model call in a live phone call, and a test
that passes today passes tomorrow. A vector store would add recall we cannot
currently measure and non-determinism we would have to work around — see
docs/DEFERRED.md before reaching for one.

The scoring errs towards handing off. An answer either exists and is read out
word for word, or none exists and the caller is offered a representative. A
confident wrong answer about an insurance claim is worse than an honest "let me
put you through to someone" (CLAUDE.md §12, §15).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.core.errors import AppError
from app.core.logging import event, get_logger
from app.core.paths import knowledge_directory

log = get_logger(__name__)

DEFAULT_FAQ_DIRECTORY = knowledge_directory()

# The four topics CLAUDE.md requires. Startup fails without them, rather than
# discovering the gap when a caller asks.
REQUIRED_TOPIC_IDS = frozenset({"office_hours", "mailing_address", "new_claim", "claims_process"})

# Words that match everything and so distinguish nothing. Note that "when" and
# "where" are deliberately absent: they are the strongest signal we have for
# office hours and the mailing address respectively.
_STOP_WORDS = frozenset(
    """a am an and any are as at be been being but by can could did do does
    doing for from get got had has have how i if in is it its just like me my
    of on or our please should so some tell that the their them then there
    these they this those to us want was we were what which who why
    will with would you your""".split()
)

_TOKEN = re.compile(r"[a-z0-9']+")
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_ANSWER_SECTION = re.compile(r"^##\s+Answer\s*$(.*?)(?=^##\s|\Z)", re.DOTALL | re.MULTILINE)


class Confidence(StrEnum):
    """How well the best match actually fits the question.

    Returned to the agent alongside the answer so it can hedge or hand off
    rather than reading a marginal match with full conviction. Only HIGH and
    MEDIUM produce an answer at all.
    """

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class FaqOutcome(StrEnum):
    ANSWER_FOUND = "ANSWER_FOUND"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    NO_MATCH = "NO_MATCH"


class FaqConfigurationError(AppError):
    code = "FAQ_CONFIGURATION_ERROR"
    message = "FAQ knowledge is not configured correctly."


# Score bands. Tuned against the questions in tests/test_faq_knowledge.py;
# move them only with a failing example in hand.
_HIGH_CONFIDENCE = 0.60
_MEDIUM_CONFIDENCE = 0.34


@dataclass(frozen=True, slots=True)
class FaqEntry:
    """One topic, loaded from one Markdown file."""

    id: str
    topic: str
    keywords: frozenset[str]
    answer: str
    source: str  # file name, so a wrong answer can be traced to its file

    @property
    def is_demo_content(self) -> bool:
        """Every shipped file is sample data, and says so in the file itself."""
        return True


@dataclass(frozen=True, slots=True)
class FaqResult:
    """A retrieval result, including how sure we are about it."""

    outcome: FaqOutcome
    confidence: Confidence
    score: float = 0.0
    entry: FaqEntry | None = None
    # What matched, for logs and for explaining a surprising result.
    matched_terms: tuple[str, ...] = ()

    @property
    def is_answered(self) -> bool:
        return self.outcome is FaqOutcome.ANSWER_FOUND and self.entry is not None


class FaqService:
    """Answers the supported topics, and nothing else."""

    def __init__(self, entries: list[FaqEntry]) -> None:
        if not entries:
            raise FaqConfigurationError("No FAQ entries were loaded.")
        self._entries = entries

    @property
    def topics(self) -> list[str]:
        """What the agent can truthfully offer to help with."""
        return [entry.topic for entry in self._entries]

    @property
    def entries(self) -> list[FaqEntry]:
        return list(self._entries)

    def search(self, question: str) -> FaqResult:
        """Find the configured answer for a question, if one fits well enough."""
        tokens = _tokenise(question)
        if not tokens:
            return FaqResult(FaqOutcome.NO_MATCH, Confidence.NONE)

        best: FaqEntry | None = None
        best_rank = (0.0, 0.0)
        best_matches: frozenset[str] = frozenset()

        for entry in self._entries:
            matched = tokens & entry.keywords
            if not matched:
                continue

            # Coverage: how much of the question this entry accounts for.
            # Precision: how much of the entry's own vocabulary was used —
            # only a tie-break, so a narrow topic wins over a broad one that
            # happens to share a word. Without it, ties fall to file order,
            # which is arbitrary and moves when a file is renamed.
            coverage = len(matched) / len(tokens)
            precision = len(matched) / len(entry.keywords)
            rank = (coverage, precision)

            if rank > best_rank:
                best, best_rank, best_matches = entry, rank, matched

        best_score = best_rank[0]
        confidence = _confidence_for(best_score)

        if best is None or confidence in (Confidence.LOW, Confidence.NONE):
            outcome = (
                FaqOutcome.NO_MATCH if confidence is Confidence.NONE else FaqOutcome.LOW_CONFIDENCE
            )
            log.info(
                "faq.lookup",
                extra=event(
                    outcome=outcome,
                    confidence=confidence,
                    score=round(best_score, 3),
                    best_candidate=best.id if best else None,
                ),
            )
            return FaqResult(outcome, confidence, score=best_score)

        log.info(
            "faq.lookup",
            extra=event(
                outcome=FaqOutcome.ANSWER_FOUND,
                confidence=confidence,
                entry=best.id,
                source=best.source,
                score=round(best_score, 3),
            ),
        )
        return FaqResult(
            FaqOutcome.ANSWER_FOUND,
            confidence,
            score=best_score,
            entry=best,
            matched_terms=tuple(sorted(best_matches)),
        )


def _confidence_for(score: float) -> Confidence:
    if score >= _HIGH_CONFIDENCE:
        return Confidence.HIGH
    if score >= _MEDIUM_CONFIDENCE:
        return Confidence.MEDIUM
    if score > 0:
        return Confidence.LOW
    return Confidence.NONE


def _tokenise(text: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(text.lower())) - _STOP_WORDS


# --- loading ------------------------------------------------------------------


def load_faq_entries(directory: Path | None = None) -> list[FaqEntry]:
    """Read every knowledge file in `directory`.

    Markdown files without a frontmatter block are skipped: `knowledge/` also
    holds a README and JSON configuration, and a directory listing should not
    become a schema. A file *with* frontmatter but missing required fields is a
    hard failure — that is a broken knowledge file, not an unrelated one.
    """
    source = directory or DEFAULT_FAQ_DIRECTORY

    if not source.is_dir():
        raise FaqConfigurationError("FAQ knowledge directory not found.", path=str(source))

    entries: list[FaqEntry] = []
    for path in sorted(source.glob("*.md")):
        entry = _load_entry(path)
        if entry is not None:
            entries.append(entry)

    if not entries:
        raise FaqConfigurationError("No FAQ knowledge files were found.", path=str(source))

    identifiers = [entry.id for entry in entries]
    duplicates = sorted({i for i in identifiers if identifiers.count(i) > 1})
    if duplicates:
        raise FaqConfigurationError(
            "FAQ entry ids must be unique.", path=str(source), duplicates=duplicates
        )

    missing = sorted(REQUIRED_TOPIC_IDS - set(identifiers))
    if missing:
        raise FaqConfigurationError(
            "Required FAQ topics are missing.", path=str(source), missing_topics=missing
        )

    log.info(
        "faq.loaded",
        extra=event(path=str(source), entries=len(entries), topics=sorted(identifiers)),
    )
    return entries


def _load_entry(path: Path) -> FaqEntry | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FaqConfigurationError(
            "FAQ knowledge file could not be read.", path=str(path)
        ) from exc

    frontmatter_match = _FRONTMATTER.match(text)
    if frontmatter_match is None:
        # Not a knowledge file — a README, or notes someone left here.
        log.debug("faq.file_skipped", extra=event(file=path.name, reason="no_frontmatter"))
        return None

    fields = _parse_frontmatter(frontmatter_match.group(1))
    body = text[frontmatter_match.end() :]

    entry_id = fields.get("id", "").strip()
    topic = fields.get("topic", "").strip()
    keywords = _split_keywords(fields.get("keywords", ""))
    answer = _extract_answer(body)

    missing = [
        name
        for name, value in (
            ("id", entry_id),
            ("topic", topic),
            ("keywords", keywords),
            ("## Answer", answer),
        )
        if not value
    ]
    if missing:
        raise FaqConfigurationError(
            "FAQ knowledge file is incomplete.", path=str(path), missing_fields=missing
        )

    return FaqEntry(
        id=entry_id,
        topic=topic,
        keywords=frozenset(keywords),
        answer=answer,
        source=path.name,
    )


def _parse_frontmatter(block: str) -> dict[str, str]:
    """`key: value` lines. Deliberately not YAML — no dependency, no surprises."""
    fields: dict[str, str] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        fields[key.strip().lower()] = value.strip()
    return fields


def _split_keywords(raw: str) -> list[str]:
    return [word.strip().lower() for word in raw.split(",") if word.strip()]


def _extract_answer(body: str) -> str:
    """The `## Answer` section, flattened to one spoken paragraph.

    Only this section is ever read aloud. Demo disclaimers and maintainer notes
    live in the same file precisely because they are *not* part of the answer.
    """
    match = _ANSWER_SECTION.search(body)
    if match is None:
        return ""
    return " ".join(match.group(1).split())
