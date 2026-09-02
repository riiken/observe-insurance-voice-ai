#!/usr/bin/env python
"""Generate docs/FAILURE-MATRIX.md from the failure catalogue.

The matrix is derived, not written, so it cannot drift from the code. A test
asserts the catalogue covers every error code the system defines; this turns
that catalogue into the table.

    python scripts/generate_failure_matrix.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.core.failures import FAILURE_CATALOGUE, FailureClass

_CLASS_NOTES = {
    FailureClass.TRANSIENT_UPSTREAM: "A dependency is briefly unwell. Retry.",
    FailureClass.PERMANENT_UPSTREAM: (
        "A dependency will keep saying no. Do not retry — a human must change "
        "something."
    ),
    FailureClass.DATA_QUALITY: (
        "The data we were given is unusable. Our problem, never reported as "
        "'no such record'."
    ),
    FailureClass.NOT_FOUND: (
        "The dependency answered correctly, and the answer is that there is no "
        "such record. **Not a failure.**"
    ),
    FailureClass.CALLER_INPUT: "The caller gave something we could not use. Normal.",
    FailureClass.AUTHORIZATION: "The session does not permit this.",
    FailureClass.CONFIGURATION: "This deployment is set up wrongly.",
    FailureClass.INTERNAL: "Our bug.",
}


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render() -> str:
    lines = [
        "# Failure matrix",
        "",
        (
            "**Generated from "
            "[`app/core/failures.py`](../backend/app/core/failures.py) — do not "
            "edit by hand.** Regenerate with "
            "`python scripts/generate_failure_matrix.py`."
        ),
        "",
        (
            "A test asserts that every error code the system defines appears in "
            "the catalogue, so a new failure mode cannot ship without a decision "
            "about how it is handled."
        ),
        "",
        (
            "The distinction the whole matrix is arranged around: **an "
            "infrastructure failure is never a business outcome.** A Google "
            "Sheets timeout is not a customer who does not exist, and the two "
            "never share a classification, a log level, or a sentence to a "
            "caller."
        ),
        "",
    ]

    for failure_class in FailureClass:
        modes = [
            m for m in FAILURE_CATALOGUE.values() if m.failure_class is failure_class
        ]
        if not modes:
            continue

        lines += [
            f"## {failure_class.value.replace('_', ' ').title()}",
            "",
            _CLASS_NOTES[failure_class],
            "",
            "| Failure | Detection | User response | Recovery | Retried | Logged |",
            "| ------- | --------- | ------------- | -------- | ------- | ------ |",
        ]
        for mode in sorted(modes, key=lambda m: m.code):
            lines.append(
                f"| `{mode.code}` | {_escape(mode.detection)} "
                f"| {_escape(mode.user_response)} | {_escape(mode.recovery)} "
                f"| {'yes' if mode.retried else 'no'} | {mode.severity.value} |"
            )
        lines.append("")

    lines += [
        "---",
        "",
        "## Budgets",
        "",
        "| | Value | Why |",
        "| --- | --- | --- |",
        (
            "| Per-attempt timeout, in call | `HTTP_TIMEOUT_SECONDS` (10s) | One "
            "Sheets read |"
        ),
        (
            "| **Total budget, in call** | `VOICE_TURN_BUDGET_SECONDS` (6s) | A "
            "caller is listening to silence. Three attempts at ten seconds is "
            "thirty seconds of nothing, by which point retrying is pointless — "
            "they have gone. Failing fast leaves time to apologise and offer a "
            "person. |"
        ),
        (
            "| Per-attempt timeout, post-call | `POSTCALL_TIMEOUT_SECONDS` (20s) "
            "| Nobody is waiting, so it may take longer |"
        ),
        "| Attempts | `HTTP_MAX_RETRIES` (2) | Bounded |",
        (
            "| Backoff | `HTTP_BACKOFF_BASE_SECONDS` (0.2s), exponential, **full "
            "jitter**, capped at 5s | Without jitter, every concurrent call "
            "hitting the same rate limit retries in lockstep and recreates the "
            "burst |"
        ),
        "",
        "## What a caller never hears",
        "",
        (
            "Asserted for every failure branch in "
            "[`test_failure_handling.py`]"
            "(../backend/tests/test_failure_handling.py):"
        ),
        "",
        "- HTTP status codes, ours or an upstream's",
        "- The words *Google*, *sheet*, *spreadsheet*, or any library name",
        "- Stack traces, exception names, `None`, or JSON punctuation",
        (
            "- Which specific check refused them — every authorization refusal "
            "is worded identically, so probing reveals nothing"
        ),
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    output = REPO_ROOT / "docs" / "FAILURE-MATRIX.md"
    output.write_text(render(), encoding="utf-8")
    print(
        f"wrote {output.relative_to(REPO_ROOT)} ({len(FAILURE_CATALOGUE)} failure modes)"
    )
