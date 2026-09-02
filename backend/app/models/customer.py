"""Customer domain model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Customer:
    """A customer as the rest of the application is allowed to see them.

    Deliberately does **not** carry `verification_value`. The secret used to
    authenticate a caller never leaves the repository, so no service, tool,
    prompt, log line or API response can leak it even by accident — the field
    simply does not exist outside the row parser.

    It also carries no claim data: customer lookup happens *before*
    authentication, so anything on this object is disclosable to an
    unauthenticated caller.
    """

    customer_id: str
    full_name: str
    phone_number: str  # normalised to E.164

    @property
    def first_name(self) -> str:
        """For a natural greeting: 'Thanks, Maria.'"""
        return self.full_name.split(" ", 1)[0] if self.full_name else ""
