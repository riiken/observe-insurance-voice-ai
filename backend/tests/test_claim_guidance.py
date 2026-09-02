"""Configured claim guidance: it must be complete, and it must be the source."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.enums import ClaimStatus
from app.services.guidance import (
    DEFAULT_GUIDANCE_PATH,
    GuidanceConfigurationError,
    load_claim_guidance,
)


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "guidance.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _valid_payload() -> dict:
    return json.loads(DEFAULT_GUIDANCE_PATH.read_text(encoding="utf-8"))


def test_the_shipped_guidance_file_loads() -> None:
    guidance = load_claim_guidance()

    assert guidance.submission.portal_url
    assert guidance.submission.mailing_address


def test_every_supported_status_is_configured() -> None:
    """A status with no guidance would leave the agent improvising mid-call."""
    guidance = load_claim_guidance()

    for status in ClaimStatus:
        assert guidance.for_status(status).next_step
        assert guidance.for_status(status).speech_next_step


def test_a_missing_status_fails_at_load(tmp_path: Path) -> None:
    payload = _valid_payload()
    del payload["statuses"]["APPROVED"]

    with pytest.raises(GuidanceConfigurationError) as caught:
        load_claim_guidance(_write(tmp_path, payload))

    assert "APPROVED" in caught.value.context["missing_statuses"]


def test_an_empty_next_step_is_rejected(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["statuses"]["APPROVED"]["next_step"] = ""

    with pytest.raises(GuidanceConfigurationError):
        load_claim_guidance(_write(tmp_path, payload))


def test_missing_submission_instructions_are_rejected(tmp_path: Path) -> None:
    """Documents-required guidance is worthless without somewhere to send them."""
    payload = _valid_payload()
    del payload["submission"]["mailing_address"]

    with pytest.raises(GuidanceConfigurationError):
        load_claim_guidance(_write(tmp_path, payload))


def test_a_missing_file_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(GuidanceConfigurationError) as caught:
        load_claim_guidance(tmp_path / "nope.json")

    assert "not found" in caught.value.message.lower()


def test_unreadable_json_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "guidance.json"
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(GuidanceConfigurationError):
        load_claim_guidance(path)


def test_the_comment_block_is_ignored(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["_comment"] = ["anything at all"]

    assert load_claim_guidance(_write(tmp_path, payload)) is not None
