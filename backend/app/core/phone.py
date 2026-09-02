"""Phone number normalisation.

A caller reads their number aloud and the voice platform transcribes it, so the
same account arrives as "+1 (555) 010-1234", "555 010 1234", "1-555-010-1234"
or "five five five..." already digitised. All of those must reach the same row
in the customer sheet, so every lookup normalises to E.164 first.

Pure functions, no I/O — this is the one piece of Phase 2 that is trivially
exhaustively testable, and it is where lookups most often go wrong.
"""

from __future__ import annotations

import re

# E.164 allows at most 15 digits; 8 is below any plausible real subscriber
# number and filters out transcription fragments.
_MIN_DIGITS = 8
_MAX_DIGITS = 15

_NON_DIALLABLE = re.compile(r"[^\d+]")
# A trailing extension is not part of the subscriber number.
_EXTENSION = re.compile(r"\b(?:ext|extension|x)\.?\s*\d+\s*$", re.IGNORECASE)


def normalize_phone(raw: str | None, *, default_country_code: str = "+1") -> str | None:
    """Return `raw` as an E.164 string, or None if it cannot be one.

    None means "this is not a phone number" — it never means "no such customer".
    Callers must keep those two apart.
    """
    if not raw:
        return None

    text = _EXTENSION.sub("", str(raw).strip())
    # A '+' is only meaningful as the first character of an international number.
    had_plus = text.lstrip().startswith("+")
    digits = _NON_DIALLABLE.sub("", text).replace("+", "")

    if not digits:
        return None

    if had_plus:
        candidate = digits
    else:
        country_digits = default_country_code.lstrip("+")
        if digits.startswith(country_digits) and len(digits) > len(country_digits):
            # A national number already carrying its country code, e.g. 15550101234.
            candidate = digits
        else:
            candidate = f"{country_digits}{digits}"

    if not _MIN_DIGITS <= len(candidate) <= _MAX_DIGITS:
        return None

    return f"+{candidate}"


def mask_phone(phone: str | None) -> str:
    """Redact a number for logs and error context: '+15550101234' -> '***1234'."""
    if not phone:
        return "***"
    return f"***{phone[-4:]}" if len(phone) > 4 else "***"
