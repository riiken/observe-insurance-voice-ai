"""Turning spreadsheet rows into domain objects.

A spreadsheet is hand-edited, so this layer assumes nothing: columns get
reordered, cells get left blank, a status gets typed in lower case, someone adds
a note column. Two different failures are kept apart:

- **A malformed row** is skipped. One bad row must not deny service to every
  other customer, so it is logged (by position, never by content) and the scan
  continues.
- **A malformed header** is an integration error. If the columns we need are not
  there, we cannot tell a missing customer from an unreadable sheet — and
  guessing would break the one rule this integration exists to uphold.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.core.errors import IntegrationError
from app.core.logging import event, get_logger
from app.core.phone import normalize_phone
from app.models.claim import Claim
from app.models.customer import Customer
from app.models.enums import ClaimStatus

INTEGRATION_NAME = "google-sheets"

log = get_logger(__name__)

CUSTOMER_COLUMNS = ("customer_id", "full_name", "phone_number", "verification_value")
CLAIM_COLUMNS = ("claim_id", "customer_id", "status", "required_documents", "last_updated")

# Accepted date formats, most-specific first. Sheets renders dates per locale.
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y")

# Required documents are a single cell: "Police report; Repair estimate".
_DOCUMENT_SEPARATORS = (";", "|", ",")


@dataclass(frozen=True, slots=True)
class CustomerRow:
    """A parsed customer row, including the secret.

    Stays inside the repository. `to_customer()` produces the object everything
    else is allowed to hold — which is how the verification value is kept from
    ever reaching a service, a prompt or a log.
    """

    customer_id: str
    full_name: str
    phone_number: str  # normalised E.164
    verification_value: str

    def to_customer(self) -> Customer:
        return Customer(
            customer_id=self.customer_id,
            full_name=self.full_name,
            phone_number=self.phone_number,
        )


def index_header(header: list[str], required: tuple[str, ...], *, sheet: str) -> dict[str, int]:
    """Map column name to position, so column order in the sheet does not matter.

    Raises `IntegrationError` if a required column is absent.
    """
    index = {name.strip().lower(): position for position, name in enumerate(header) if name.strip()}
    missing = [column for column in required if column not in index]

    if missing:
        raise IntegrationError(
            f"The {sheet} sheet is missing required columns.",
            integration=INTEGRATION_NAME,
            sheet=sheet,
            missing_columns=missing,
        )

    return index


def _cell(row: list[str], index: dict[str, int], column: str) -> str:
    """Read a cell by column name. Trailing empty cells are omitted by the API."""
    position = index[column]
    return row[position].strip() if position < len(row) else ""


def parse_customer_row(
    row: list[str], index: dict[str, int], *, row_number: int, default_country_code: str
) -> CustomerRow | None:
    """Parse one customer row, or None if it is unusable."""
    customer_id = _cell(row, index, "customer_id")
    full_name = _cell(row, index, "full_name")
    raw_phone = _cell(row, index, "phone_number")
    verification_value = _cell(row, index, "verification_value")

    missing = [
        name
        for name, value in (
            ("customer_id", customer_id),
            ("full_name", full_name),
            ("phone_number", raw_phone),
            ("verification_value", verification_value),
        )
        if not value
    ]
    if missing:
        _log_malformed("Customers", row_number, missing_fields=missing)
        return None

    phone_number = normalize_phone(raw_phone, default_country_code=default_country_code)
    if phone_number is None:
        # An unusable number in the sheet, not from the caller.
        _log_malformed("Customers", row_number, missing_fields=["phone_number:unparseable"])
        return None

    return CustomerRow(
        customer_id=customer_id,
        full_name=full_name,
        phone_number=phone_number,
        verification_value=verification_value,
    )


def parse_claim_row(row: list[str], index: dict[str, int], *, row_number: int) -> Claim | None:
    """Parse one claim row, or None if it is unusable."""
    claim_id = _cell(row, index, "claim_id")
    customer_id = _cell(row, index, "customer_id")
    raw_status = _cell(row, index, "status")

    missing = [
        name
        for name, value in (
            ("claim_id", claim_id),
            ("customer_id", customer_id),
            ("status", raw_status),
        )
        if not value
    ]
    if missing:
        _log_malformed("Claims", row_number, missing_fields=missing)
        return None

    status = parse_claim_status(raw_status)
    if status is None:
        # An unrecognised status must not be guessed at: telling a caller their
        # claim is approved when the sheet says something we do not understand
        # is the worst possible failure mode.
        _log_malformed("Claims", row_number, missing_fields=["status:unrecognised"])
        return None

    return Claim(
        claim_id=claim_id,
        customer_id=customer_id,
        status=status,
        required_documents=parse_documents(_cell(row, index, "required_documents")),
        last_updated=parse_date(_cell(row, index, "last_updated")),
    )


def parse_claim_status(raw: str) -> ClaimStatus | None:
    """Accept 'Documents Required', 'documents_required', 'DOCUMENTS-REQUIRED'."""
    normalised = raw.strip().upper().replace(" ", "_").replace("-", "_")
    try:
        return ClaimStatus(normalised)
    except ValueError:
        return None


def parse_documents(raw: str) -> tuple[str, ...]:
    """Split a document list cell, tolerating any of the usual separators."""
    if not raw.strip():
        return ()

    text = raw
    for separator in _DOCUMENT_SEPARATORS[1:]:
        text = text.replace(separator, _DOCUMENT_SEPARATORS[0])

    return tuple(item.strip() for item in text.split(_DOCUMENT_SEPARATORS[0]) if item.strip())


def parse_date(raw: str) -> date | None:
    """A date we cannot read is dropped, not guessed — it is never load-bearing."""
    text = raw.strip()
    if not text:
        return None

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _log_malformed(sheet: str, row_number: int, *, missing_fields: list[str]) -> None:
    """Log position and field names only — never cell contents, which are PII."""
    log.warning(
        "sheets.row_malformed",
        extra=event(sheet=sheet, row=row_number, fields=missing_fields),
    )
