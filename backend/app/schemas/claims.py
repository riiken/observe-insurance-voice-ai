"""Structured claim information handed to the conversation layer."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.models.enums import ClaimStatus


class SubmissionInstructionsView(BaseModel):
    """How to send documents in. Copied from configuration, never generated."""

    portal_url: str
    email: str
    mailing_address: str
    reference_instruction: str
    turnaround: str


class ClaimStatusView(BaseModel):
    """The claim, as the agent is allowed to know it.

    Present only for an authenticated session. `next_step` and
    `submission_instructions` come from `knowledge/claim_guidance.json`, so
    everything the agent can say about what happens next is reviewable content
    rather than model output.
    """

    claim_id: str
    status: ClaimStatus
    required_documents: list[str] = Field(default_factory=list)
    last_updated: date | None = None
    next_step: str

    # Populated only when documents are outstanding — there is nothing to
    # submit otherwise, and reading out an address unprompted is noise.
    submission_instructions: SubmissionInstructionsView | None = None
