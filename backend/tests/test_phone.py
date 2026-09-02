"""Phone normalisation — the same account must be reachable however it is said."""

from __future__ import annotations

import pytest

from app.core.phone import mask_phone, normalize_phone


@pytest.mark.parametrize(
    "spoken",
    [
        "+15550101234",
        "+1 555 010 1234",
        "+1 (555) 010-1234",
        "1-555-010-1234",
        "15550101234",
        "555-010-1234",
        "555 010 1234",
        "(555) 010 1234",
        "  555.010.1234  ",
        "555 010 1234 ext. 22",
        "5550101234 x104",
    ],
)
def test_every_spoken_form_reaches_the_same_number(spoken: str) -> None:
    assert normalize_phone(spoken) == "+15550101234"


def test_international_numbers_keep_their_country_code() -> None:
    assert normalize_phone("+44 20 7946 0958") == "+442079460958"


def test_default_country_code_is_configurable() -> None:
    assert normalize_phone("20 7946 0958", default_country_code="+44") == "+442079460958"


@pytest.mark.parametrize(
    "unusable",
    [None, "", "   ", "abc", "not a number", "12", "1234567", "+", "-", "12345678901234567890"],
)
def test_unusable_input_returns_none(unusable: str | None) -> None:
    """None means 'not a phone number'. It must never mean 'no such customer'."""
    assert normalize_phone(unusable) is None


def test_mask_phone_keeps_only_the_last_four_digits() -> None:
    assert mask_phone("+15550101234") == "***1234"
    assert mask_phone(None) == "***"
    assert mask_phone("12") == "***"
