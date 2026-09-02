"""Claim domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.models.enums import ClaimStatus


@dataclass(frozen=True, slots=True)
class Claim:
    """A claim. Only ever handed out for an authenticated session."""

    claim_id: str
    customer_id: str
    status: ClaimStatus
    required_documents: tuple[str, ...] = field(default_factory=tuple)
    last_updated: date | None = None

    @property
    def needs_documents(self) -> bool:
        return self.status is ClaimStatus.DOCUMENTS_REQUIRED
