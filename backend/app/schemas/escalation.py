"""Structured escalation result handed to the conversation layer."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import EscalationReason, EscalationStatus


class EscalationView(BaseModel):
    """A raised escalation, as the agent is allowed to see it.

    Carries **no claim information**: an escalation can be raised by an
    unverified caller, so everything here must be safe for one to cause.
    Deliberately no claim id, no claim status, no customer name.
    """

    escalation_id: str
    reason: EscalationReason
    status: EscalationStatus
    created_at: datetime
    is_emergency: bool = False
    transfer_available: bool = Field(
        default=False,
        description="Whether the platform can actually hand the call over.",
    )
