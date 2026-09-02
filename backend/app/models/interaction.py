"""The post-call interaction record (Integration #2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.models.enums import ConversationOutcome, Sentiment

# Column order in the Interactions sheet. The header row must match this
# exactly; the repository verifies it rather than trusting position.
INTERACTION_COLUMNS = (
    "call_id",
    "timestamp",
    "caller_name",
    "caller_phone",
    "customer_id",
    "claim_id",
    "authenticated",
    "resolution",
    "escalated",
    "escalation_reason",
    "sentiment",
    "call_summary",
)


@dataclass(frozen=True, slots=True)
class InteractionRecord:
    """What happened on one call, written once when it ends.

    `call_id` is the idempotency key: a repeated end-of-call event must not
    produce a second row.

    Everything here is derived from state we observed. Nothing is inferred from
    a transcript, so the record cannot describe an event that did not happen.
    """

    call_id: str
    timestamp: datetime
    caller_name: str
    call_summary: str
    sentiment: Sentiment

    caller_phone: str | None = None
    customer_id: str | None = None
    claim_id: str | None = None
    authenticated: bool = False
    resolution: ConversationOutcome | None = None
    escalated: bool = False
    escalation_reason: str | None = None

    def as_row(self) -> list[str]:
        """Flatten to sheet cells, in `INTERACTION_COLUMNS` order.

        Absent optional values become an empty cell rather than the string
        "None", which would read as data to anyone opening the sheet.
        """
        values = {
            "call_id": self.call_id,
            "timestamp": self.timestamp.isoformat(),
            "caller_name": self.caller_name,
            "caller_phone": self.caller_phone,
            "customer_id": self.customer_id,
            "claim_id": self.claim_id,
            "authenticated": "TRUE" if self.authenticated else "FALSE",
            "resolution": self.resolution,
            "escalated": "TRUE" if self.escalated else "FALSE",
            "escalation_reason": self.escalation_reason,
            "sentiment": self.sentiment,
            "call_summary": self.call_summary,
        }
        return [_cell(values[column]) for column in INTERACTION_COLUMNS]


def _cell(value: object) -> str:
    if value is None:
        return ""
    return str(value)
