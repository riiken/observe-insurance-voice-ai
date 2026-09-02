"""The voice layer: what a caller actually hears.

These assertions are about speech, not data. A caller cannot skim, cannot
re-read, and cannot see punctuation — so the rules in CLAUDE.md §16 are checked
here as properties of the rendered string.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models.claim import Claim
from app.models.enums import ClaimStatus
from app.services.guidance import ClaimGuidance, load_claim_guidance
from app.services.voice import (
    render_claim_status,
    speak_date,
    speak_list,
    speak_reference,
)

ALL_STATUSES = list(ClaimStatus)


@pytest.fixture
def guidance() -> ClaimGuidance:
    return load_claim_guidance()


def _claim(status: ClaimStatus, **overrides: object) -> Claim:
    fields: dict = {
        "claim_id": "CLM-88402",
        "customer_id": "CUST-1002",
        "status": status,
        "required_documents": (),
        "last_updated": date(2026, 8, 28),
    }
    fields.update(overrides)
    return Claim(**fields)  # type: ignore[arg-type]


# --- shape --------------------------------------------------------------------


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_every_status_renders_a_sentence(guidance: ClaimGuidance, status: ClaimStatus) -> None:
    documents = ("Police report",) if status is ClaimStatus.DOCUMENTS_REQUIRED else ()

    speech = render_claim_status(_claim(status, required_documents=documents), guidance)

    assert speech
    assert speech[0].isupper()
    assert speech.rstrip().endswith((".", "?"))


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_speech_contains_no_markup_or_raw_data(
    guidance: ClaimGuidance, status: ClaimStatus
) -> None:
    """No markdown, no JSON, no enum names read aloud (CLAUDE.md §16)."""
    documents = ("Police report",) if status is ClaimStatus.DOCUMENTS_REQUIRED else ()

    speech = render_claim_status(_claim(status, required_documents=documents), guidance)

    for artefact in ("{", "}", "[", "]", "*", "#", "_", "ClaimStatus", "None", "2026-08-28"):
        assert artefact not in speech


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_speech_stays_short_enough_to_listen_to(
    guidance: ClaimGuidance, status: ClaimStatus
) -> None:
    documents = ("Police report",) if status is ClaimStatus.DOCUMENTS_REQUIRED else ()

    speech = render_claim_status(_claim(status, required_documents=documents), guidance)

    assert len(speech.split()) <= 60


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_speech_asks_at_most_one_question(guidance: ClaimGuidance, status: ClaimStatus) -> None:
    """One question at a time, or the caller answers only the last one."""
    documents = ("Police report",) if status is ClaimStatus.DOCUMENTS_REQUIRED else ()

    speech = render_claim_status(_claim(status, required_documents=documents), guidance)

    assert speech.count("?") <= 1


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_speech_never_promises_an_outcome_or_a_date(
    guidance: ClaimGuidance, status: ClaimStatus
) -> None:
    """Never promise approval, payment timing or a guaranteed outcome."""
    documents = ("Police report",) if status is ClaimStatus.DOCUMENTS_REQUIRED else ()

    speech = render_claim_status(_claim(status, required_documents=documents), guidance).lower()

    for promise in (
        "will be approved",
        "guarantee",
        "guaranteed",
        "you'll receive payment",
        "paid out",
        "definitely",
        "i promise",
    ):
        assert promise not in speech


# --- content ------------------------------------------------------------------


def test_under_review_says_so_and_gives_the_date(guidance: ClaimGuidance) -> None:
    speech = render_claim_status(_claim(ClaimStatus.UNDER_REVIEW), guidance)

    assert "under review" in speech.lower()
    assert "August the 28th" in speech


def test_a_claim_with_no_date_simply_omits_it(guidance: ClaimGuidance) -> None:
    speech = render_claim_status(_claim(ClaimStatus.APPROVED, last_updated=None), guidance)

    assert "last updated" not in speech.lower()
    assert "approved" in speech.lower()


def test_documents_required_names_each_document_naturally(guidance: ClaimGuidance) -> None:
    speech = render_claim_status(
        _claim(
            ClaimStatus.DOCUMENTS_REQUIRED,
            required_documents=("Police report", "Repair estimate"),
        ),
        guidance,
    )

    assert "a police report and a repair estimate" in speech


def test_documents_required_offers_to_explain_how_to_send_them(
    guidance: ClaimGuidance,
) -> None:
    speech = render_claim_status(
        _claim(ClaimStatus.DOCUMENTS_REQUIRED, required_documents=("Police report",)),
        guidance,
    )

    assert speech.rstrip().endswith("?")


def test_an_empty_document_list_admits_the_gap_instead_of_inventing(
    guidance: ClaimGuidance,
) -> None:
    speech = render_claim_status(
        _claim(ClaimStatus.DOCUMENTS_REQUIRED, required_documents=()), guidance
    )

    assert "representative" in speech.lower()
    for invented in ("police report", "repair estimate", "receipt", "photograph"):
        assert invented not in speech.lower()


# --- helpers ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (date(2026, 8, 1), "August the 1st"),
        (date(2026, 8, 2), "August the 2nd"),
        (date(2026, 8, 3), "August the 3rd"),
        (date(2026, 8, 4), "August the 4th"),
        (date(2026, 8, 11), "August the 11th"),
        (date(2026, 8, 12), "August the 12th"),
        (date(2026, 8, 13), "August the 13th"),
        (date(2026, 8, 21), "August the 21st"),
        (date(2026, 12, 31), "December the 31st"),
    ],
)
def test_dates_are_spoken_not_spelled(value: date, expected: str) -> None:
    """A TTS engine handed '2026-08-28' reads digits; the caller learns nothing."""
    assert speak_date(value) == expected


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ([], ""),
        (["one"], "one"),
        (["one", "two"], "one and two"),
        (["one", "two", "three"], "one, two and three"),
    ],
)
def test_lists_are_joined_the_way_a_person_speaks(items: list[str], expected: str) -> None:
    assert speak_list(items) == expected


def test_references_are_broken_up_so_they_can_be_written_down() -> None:
    assert speak_reference("CLM-88402") == "CLM 88402"
