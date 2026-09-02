"""FAQ knowledge: loading, retrieval, confidence, and refusing to invent.

The most important tests here are the ones that assert an answer is *not*
given. A confident wrong answer about an insurance claim is worse than an
honest hand-off.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.services.faq import (
    DEFAULT_FAQ_DIRECTORY,
    REQUIRED_TOPIC_IDS,
    Confidence,
    FaqConfigurationError,
    FaqOutcome,
    FaqService,
    load_faq_entries,
)
from app.tools.base import ToolOutcome
from app.tools.faq_tool import SearchFaqTool

CALL = "call-1"


@pytest.fixture
def faq() -> FaqService:
    return FaqService(load_faq_entries())


@pytest.fixture
def tool(faq: FaqService) -> SearchFaqTool:
    return SearchFaqTool(faq)


@pytest.fixture
def knowledge_dir(tmp_path: Path) -> Path:
    """A writable copy of the shipped knowledge, for corruption tests."""
    destination = tmp_path / "knowledge"
    destination.mkdir()
    for path in DEFAULT_FAQ_DIRECTORY.glob("*.md"):
        shutil.copy(path, destination / path.name)
    return destination


# --- the knowledge files ------------------------------------------------------


def test_every_required_topic_has_a_file() -> None:
    entries = {entry.id: entry for entry in load_faq_entries()}

    assert REQUIRED_TOPIC_IDS <= set(entries)


@pytest.mark.parametrize(
    ("topic_id", "filename"),
    [
        ("office_hours", "office_hours.md"),
        ("mailing_address", "mailing_address.md"),
        ("new_claim", "new_claim.md"),
        ("claims_process", "claims_process.md"),
    ],
)
def test_each_required_topic_comes_from_its_own_file(topic_id: str, filename: str) -> None:
    entry = next(e for e in load_faq_entries() if e.id == topic_id)

    assert entry.source == filename
    assert entry.answer
    assert entry.topic


def test_every_file_marks_itself_as_demo_content() -> None:
    """Fictional information must never read as real company policy."""
    for path in DEFAULT_FAQ_DIRECTORY.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue  # README and other non-knowledge files
        assert "DEMO CONTENT" in text
        assert "fictional" in text.lower()


def test_the_demo_disclaimer_is_never_spoken(faq: FaqService) -> None:
    """It belongs in the file for maintainers, not in a caller's ear."""
    for entry in faq.entries:
        assert "DEMO" not in entry.answer.upper()
        assert "fictional" not in entry.answer.lower()
        assert "maintainer" not in entry.answer.lower()


def test_answers_are_speakable(faq: FaqService) -> None:
    """Every answer is read aloud verbatim by a text-to-speech engine."""
    for entry in faq.entries:
        for artefact in ("{", "}", "[", "]", "*", "#", "|", ">", "\n"):
            assert artefact not in entry.answer, f"{entry.source} contains {artefact!r}"
        assert entry.answer[0].isupper()
        assert entry.answer.rstrip().endswith(".")


def test_answers_never_promise_an_outcome(faq: FaqService) -> None:
    for entry in faq.entries:
        lowered = entry.answer.lower()
        for promise in ("guarantee", "will be approved", "you will receive", "definitely"):
            assert promise not in lowered


# --- retrieval: each required topic -------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What are your office hours?", "office_hours"),
        ("When are you open?", "office_hours"),
        ("Are you open on Saturday?", "office_hours"),
        ("When can I reach you?", "office_hours"),
        ("What time do you close?", "office_hours"),
        ("What's your mailing address?", "mailing_address"),
        ("Where do I send a letter?", "mailing_address"),
        ("What's your postal address?", "mailing_address"),
        ("How do I start a new claim?", "new_claim"),
        ("I want to file a claim", "new_claim"),
        ("How do I report an accident?", "new_claim"),
        ("How does the claims process work?", "claims_process"),
        ("What happens after I submit?", "claims_process"),
        ("How long does a claim take?", "claims_process"),
        ("How do I upload documents?", "document_submission"),
        ("Can I email you photos?", "document_submission"),
    ],
)
def test_supported_questions_are_answered(faq: FaqService, question: str, expected: str) -> None:
    result = faq.search(question)

    assert result.is_answered
    assert result.entry is not None
    assert result.entry.id == expected


@pytest.mark.parametrize("topic_id", sorted(REQUIRED_TOPIC_IDS))
async def test_each_required_topic_is_reachable_through_the_tool(
    tool: SearchFaqTool, faq: FaqService, topic_id: str
) -> None:
    entry = next(e for e in faq.entries if e.id == topic_id)

    result = await tool(CALL, entry.topic)

    assert result.outcome is ToolOutcome.SUCCESS
    assert result.data is not None
    assert result.data.topic == entry.topic


# --- confidence ---------------------------------------------------------------


def test_a_direct_question_scores_high(faq: FaqService) -> None:
    result = faq.search("What are your office hours?")

    assert result.confidence is Confidence.HIGH
    assert result.score >= 0.6


def test_confidence_is_reported_in_the_structured_result(faq: FaqService) -> None:
    result = faq.search("What are your office hours?")

    assert result.matched_terms  # what actually matched, for explaining a result
    assert 0.0 <= result.score <= 1.0


async def test_the_tool_returns_a_relevance_indicator(tool: SearchFaqTool) -> None:
    result = await tool(CALL, "what are your office hours")

    assert result.data is not None
    assert result.data.confidence is Confidence.HIGH
    assert result.data.relevance_score >= 0.6
    assert result.data.source == "office_hours.md"
    assert result.data.is_demo_content is True


async def test_a_thin_match_is_hedged_rather_than_asserted(faq: FaqService) -> None:
    """A medium-confidence answer is still the configured one — only framed as unsure."""
    question = "I want to file a claim"
    assert faq.search(question).confidence is Confidence.MEDIUM

    result = await SearchFaqTool(faq)(CALL, question)

    assert result.outcome is ToolOutcome.SUCCESS
    assert result.speech.startswith("I think this is what you're after.")
    assert result.data is not None
    assert result.data.answer in result.speech


async def test_a_high_confidence_answer_is_not_hedged(tool: SearchFaqTool) -> None:
    result = await tool(CALL, "what are your office hours")

    assert not result.speech.startswith("I think")
    assert result.data is not None
    assert result.speech == result.data.answer


# --- low confidence and no match ----------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "What's the weather in Boston?",
        "Can you sell me a car?",
        "Am I covered for flood damage?",
        "What's my premium?",
        "Who is the chief executive?",
        "What's my policy excess?",
        "Can you cancel my policy?",
        "Do you insure motorbikes?",
        "",
        "   ",
        "?????",
    ],
)
def test_unsupported_questions_are_not_answered(faq: FaqService, question: str) -> None:
    result = faq.search(question)

    assert result.outcome is not FaqOutcome.ANSWER_FOUND
    assert result.entry is None


@pytest.mark.parametrize(
    "question",
    [
        "Am I covered for flood damage?",
        "What's my premium?",
        "Can you cancel my policy?",
        "Who is the chief executive?",
    ],
)
async def test_an_unsupported_question_invents_nothing(tool: SearchFaqTool, question: str) -> None:
    """No answer, no topic, nothing plausible to build a reply around."""
    result = await tool(CALL, question)

    assert result.outcome is ToolOutcome.NOT_FOUND
    assert result.data is None


async def test_the_limitation_is_stated_and_a_person_offered(
    tool: SearchFaqTool,
) -> None:
    result = await tool(CALL, "Am I covered for flood damage?")

    assert "not something I can help with" in result.speech
    assert "representative" in result.speech
    # It says what it *can* cover, rather than only refusing.
    assert "office hours" in result.speech.lower()


async def test_an_empty_question_is_refused_not_guessed(tool: SearchFaqTool) -> None:
    result = await tool(CALL, "   ")

    assert result.outcome is ToolOutcome.NOT_FOUND
    assert result.data is None


def test_a_single_incidental_word_is_not_enough(faq: FaqService) -> None:
    """One shared word must not drag an unrelated question into an answer."""
    result = faq.search(
        "My neighbour said the process of buying a house involves a lot of forms "
        "and paperwork and solicitors and surveys and searches and stamp duty"
    )

    assert result.confidence in (Confidence.LOW, Confidence.NONE)
    assert not result.is_answered


# --- retrieval failure --------------------------------------------------------


def test_a_missing_knowledge_directory_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(FaqConfigurationError) as caught:
        load_faq_entries(tmp_path / "does-not-exist")

    assert "not found" in caught.value.message.lower()


def test_an_empty_knowledge_directory_fails_loudly(tmp_path: Path) -> None:
    (tmp_path / "knowledge").mkdir()

    with pytest.raises(FaqConfigurationError):
        load_faq_entries(tmp_path / "knowledge")


def test_a_missing_required_topic_fails_at_startup(knowledge_dir: Path) -> None:
    """Better to fail on boot than to discover it when a caller asks."""
    (knowledge_dir / "office_hours.md").unlink()

    with pytest.raises(FaqConfigurationError) as caught:
        load_faq_entries(knowledge_dir)

    assert "office_hours" in caught.value.context["missing_topics"]


def test_a_file_with_no_answer_section_fails_loudly(knowledge_dir: Path) -> None:
    (knowledge_dir / "office_hours.md").write_text(
        "---\nid: office_hours\ntopic: Office hours\nkeywords: hours\n---\n\nNo answer here.",
        encoding="utf-8",
    )

    with pytest.raises(FaqConfigurationError) as caught:
        load_faq_entries(knowledge_dir)

    assert "## Answer" in caught.value.context["missing_fields"]


def test_a_file_with_no_keywords_fails_loudly(knowledge_dir: Path) -> None:
    """Unreachable knowledge is worse than absent knowledge — it looks present."""
    (knowledge_dir / "office_hours.md").write_text(
        "---\nid: office_hours\ntopic: Office hours\n---\n\n## Answer\n\nWe are open.",
        encoding="utf-8",
    )

    with pytest.raises(FaqConfigurationError) as caught:
        load_faq_entries(knowledge_dir)

    assert "keywords" in caught.value.context["missing_fields"]


def test_duplicate_ids_fail_loudly(knowledge_dir: Path) -> None:
    shutil.copy(knowledge_dir / "office_hours.md", knowledge_dir / "office_hours_copy.md")

    with pytest.raises(FaqConfigurationError) as caught:
        load_faq_entries(knowledge_dir)

    assert "office_hours" in caught.value.context["duplicates"]


def test_files_without_frontmatter_are_skipped_not_rejected(knowledge_dir: Path) -> None:
    """knowledge/ also holds a README; a directory listing is not a schema."""
    (knowledge_dir / "README.md").write_text("# Notes\n\nJust notes.", encoding="utf-8")

    entries = load_faq_entries(knowledge_dir)

    assert "README.md" not in {entry.source for entry in entries}
    assert REQUIRED_TOPIC_IDS <= {entry.id for entry in entries}


def test_a_service_with_no_entries_is_rejected() -> None:
    with pytest.raises(FaqConfigurationError):
        FaqService([])


async def test_a_retrieval_failure_offers_a_person_rather_than_guessing() -> None:
    """If lookup itself breaks, say nothing about the topic."""

    class _BrokenFaq:
        topics = ["Office hours"]

        def search(self, question: str) -> object:
            raise RuntimeError("index unavailable")

    result = await SearchFaqTool(_BrokenFaq())(CALL, "office hours")  # type: ignore[arg-type]

    assert result.outcome is ToolOutcome.INTEGRATION_ERROR
    assert result.data is None
    assert "representative" in result.speech
    # Must not fall back to whatever the model remembers.
    assert "Monday" not in result.speech


async def test_a_retrieval_failure_is_not_reported_as_no_answer() -> None:
    """'We don't cover that' and 'our system is down' are different sentences."""

    class _BrokenFaq:
        topics = ["Office hours"]

        def search(self, question: str) -> object:
            raise RuntimeError("boom")

    result = await SearchFaqTool(_BrokenFaq())(CALL, "office hours")  # type: ignore[arg-type]

    assert result.outcome is not ToolOutcome.NOT_FOUND
