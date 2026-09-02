"""Row parsing: the layer that absorbs a hand-edited spreadsheet."""

from __future__ import annotations

from datetime import date

import pytest

from app.core.errors import IntegrationError
from app.integrations.sheets.rows import (
    CUSTOMER_COLUMNS,
    index_header,
    parse_claim_status,
    parse_date,
    parse_documents,
)
from app.models.enums import ClaimStatus


def test_header_indexing_is_case_and_whitespace_insensitive() -> None:
    index = index_header(
        [" Customer_ID ", "Full_Name", "PHONE_NUMBER", "verification_value"],
        CUSTOMER_COLUMNS,
        sheet="Customers",
    )

    assert index["customer_id"] == 0
    assert index["phone_number"] == 2


def test_a_missing_column_raises_with_the_names_that_are_missing() -> None:
    with pytest.raises(IntegrationError) as caught:
        index_header(["customer_id", "full_name"], CUSTOMER_COLUMNS, sheet="Customers")

    assert caught.value.context["missing_columns"] == ["phone_number", "verification_value"]


def test_extra_columns_are_ignored() -> None:
    """Someone will add a notes column; that must not break the integration."""
    index = index_header(
        ["notes", *CUSTOMER_COLUMNS, "internal_flag"], CUSTOMER_COLUMNS, sheet="Customers"
    )

    assert index["customer_id"] == 1


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("Documents Required", ClaimStatus.DOCUMENTS_REQUIRED),
        ("documents_required", ClaimStatus.DOCUMENTS_REQUIRED),
        ("DOCUMENTS-REQUIRED", ClaimStatus.DOCUMENTS_REQUIRED),
        ("  Under Review  ", ClaimStatus.UNDER_REVIEW),
        ("approved", ClaimStatus.APPROVED),
    ],
)
def test_claim_status_tolerates_how_it_was_typed(written: str, expected: ClaimStatus) -> None:
    assert parse_claim_status(written) is expected


@pytest.mark.parametrize("written", ["In Arbitration", "", "closed", "APROVED"])
def test_an_unrecognised_status_is_none_rather_than_a_guess(written: str) -> None:
    assert parse_claim_status(written) is None


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("Police report; Repair estimate", ("Police report", "Repair estimate")),
        ("Police report, Repair estimate", ("Police report", "Repair estimate")),
        ("Police report | Repair estimate", ("Police report", "Repair estimate")),
        ("Police report", ("Police report",)),
        ("", ()),
        ("   ", ()),
        ("A;;B;", ("A", "B")),
    ],
)
def test_document_lists_tolerate_any_usual_separator(cell: str, expected: tuple[str, ...]) -> None:
    assert parse_documents(cell) == expected


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("2026-08-28", date(2026, 8, 28)),
        ("28/08/2026", date(2026, 8, 28)),
        ("08/28/2026", date(2026, 8, 28)),
        ("  2026-08-28 ", date(2026, 8, 28)),
    ],
)
def test_dates_are_parsed_in_the_formats_sheets_renders(cell: str, expected: date) -> None:
    assert parse_date(cell) == expected


@pytest.mark.parametrize("cell", ["", "   ", "last Tuesday", "2026-13-45"])
def test_an_unreadable_date_is_dropped_not_guessed(cell: str) -> None:
    assert parse_date(cell) is None
