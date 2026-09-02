"""Configured claim guidance.

Everything the agent is allowed to say about next steps and how to submit
documents lives in `knowledge/claim_guidance.json`, not in a prompt and not in
this file. The agent may repeat what is configured and nothing else — "do not
invent submission procedures" (CLAUDE.md §11) is enforced by there being no
code path that produces one.

Validation happens at startup and covers every `ClaimStatus`. A status with no
configured guidance is a hard failure rather than a silent gap, because the
alternative is discovering it mid-call, when the only options left are
improvising or dead air.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.core.errors import AppError
from app.core.logging import event, get_logger
from app.models.enums import ClaimStatus

log = get_logger(__name__)

# backend/app/services/guidance.py -> repository root
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GUIDANCE_PATH = _REPO_ROOT / "knowledge" / "claim_guidance.json"


class GuidanceConfigurationError(AppError):
    """The guidance file is missing, unreadable, or incomplete."""

    code = "GUIDANCE_CONFIGURATION_ERROR"
    message = "Claim guidance is not configured correctly."


class SubmissionInstructions(BaseModel):
    """How a customer sends documents in. Read out verbatim, never improvised."""

    portal_url: str = Field(min_length=1)
    email: str = Field(min_length=1)
    mailing_address: str = Field(min_length=1)
    reference_instruction: str = Field(min_length=1)
    turnaround: str = Field(min_length=1)


class StatusGuidance(BaseModel):
    """What the agent may say about one claim status."""

    summary: str = Field(min_length=1)
    next_step: str = Field(min_length=1)
    speech_next_step: str = Field(min_length=1)


class ClaimGuidance(BaseModel):
    """The whole configured vocabulary for talking about claims."""

    submission: SubmissionInstructions
    statuses: dict[ClaimStatus, StatusGuidance]

    def for_status(self, status: ClaimStatus) -> StatusGuidance:
        """Guidance for a status. Present for every status by construction."""
        return self.statuses[status]


def load_claim_guidance(path: Path | None = None) -> ClaimGuidance:
    """Read and validate the guidance file.

    Raises `GuidanceConfigurationError` rather than returning a partial object:
    starting up with half the statuses configured only moves the failure to a
    live call.
    """
    source = path or DEFAULT_GUIDANCE_PATH

    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GuidanceConfigurationError(
            "Claim guidance file not found.", path=str(source)
        ) from exc
    except (OSError, ValueError) as exc:
        raise GuidanceConfigurationError(
            "Claim guidance file could not be read.", path=str(source)
        ) from exc

    if isinstance(raw, dict):
        raw.pop("_comment", None)

    try:
        guidance = ClaimGuidance.model_validate(raw)
    except ValidationError as exc:
        raise GuidanceConfigurationError(
            "Claim guidance file is not valid.",
            path=str(source),
            errors=exc.error_count(),
        ) from exc

    missing = [status for status in ClaimStatus if status not in guidance.statuses]
    if missing:
        raise GuidanceConfigurationError(
            "Claim guidance is missing one or more statuses.",
            path=str(source),
            missing_statuses=[str(status) for status in missing],
        )

    log.info(
        "guidance.loaded",
        extra=event(path=str(source), statuses=len(guidance.statuses)),
    )
    return guidance
