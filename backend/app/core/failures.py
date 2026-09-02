"""The failure catalogue.

Every way this system can fail, in one place, with how it is detected, what the
caller hears, and what happens next.

This exists because a failure matrix written as prose rots. Here it is data: a
test asserts that every error code and every tool outcome the system can produce
appears below, so adding a new failure mode without deciding how it is handled
fails the build. `docs/FAILURE-MATRIX.md` is generated from this module.

The distinction the whole catalogue is arranged around: **an infrastructure
failure is never a business outcome.** A Sheets timeout is not a customer who
does not exist, and the two must never produce the same `FailureClass`, the same
log line, or the same sentence to a caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FailureClass(StrEnum):
    """What kind of thing went wrong. Drives triage, not phrasing.

    Deliberately separates the three that are easy to conflate:

    - `TRANSIENT_UPSTREAM` — our dependency is briefly unwell. Retry.
    - `PERMANENT_UPSTREAM` — our dependency will keep saying no. Do not retry;
      a human must change something.
    - `NOT_FOUND` — the dependency answered correctly, and the answer is that
      there is no such record. **Not a failure of ours.**
    """

    TRANSIENT_UPSTREAM = "TRANSIENT_UPSTREAM"
    PERMANENT_UPSTREAM = "PERMANENT_UPSTREAM"
    DATA_QUALITY = "DATA_QUALITY"
    NOT_FOUND = "NOT_FOUND"
    CALLER_INPUT = "CALLER_INPUT"
    AUTHORIZATION = "AUTHORIZATION"
    CONFIGURATION = "CONFIGURATION"
    INTERNAL = "INTERNAL"


class Severity(StrEnum):
    """Log level, and therefore whether anyone finds out.

    `INFO` is the normal course of a support call — a caller getting their date
    of birth wrong is not an incident. `ERROR` means a person should look.
    """

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class FailureMode:
    """One row of the failure matrix."""

    code: str
    failure_class: FailureClass
    severity: Severity
    detection: str
    user_response: str
    recovery: str
    retried: bool = False

    @property
    def is_infrastructure(self) -> bool:
        """Whether this is *our* problem rather than an answer about a record.

        `DATA_QUALITY` counts. A sheet we cannot parse is our fault, and the
        one thing it must never be reported as is "we have no record of you".
        """
        return self.failure_class in (
            FailureClass.TRANSIENT_UPSTREAM,
            FailureClass.PERMANENT_UPSTREAM,
            FailureClass.DATA_QUALITY,
            FailureClass.CONFIGURATION,
            FailureClass.INTERNAL,
        )


# Keyed by the stable code that appears in logs and in tool context.
FAILURE_CATALOGUE: dict[str, FailureMode] = {
    mode.code: mode
    for mode in (
        # --- external systems -------------------------------------------------
        FailureMode(
            code="INTEGRATION_TIMEOUT",
            failure_class=FailureClass.TRANSIENT_UPSTREAM,
            severity=Severity.ERROR,
            detection="httpx.TimeoutException in the Sheets client",
            user_response=(
                "Apologises for the system, offers a representative. "
                "Never 'we have no record of you'."
            ),
            recovery="Retried with jittered backoff inside the turn's time budget",
            retried=True,
        ),
        FailureMode(
            code="INTEGRATION_ERROR",
            failure_class=FailureClass.TRANSIENT_UPSTREAM,
            severity=Severity.ERROR,
            detection="429 / 5xx / connection failure from Sheets",
            user_response="Apologises for the system, offers a representative",
            recovery="Retried when the status is retryable; otherwise surfaced at once",
            retried=True,
        ),
        FailureMode(
            code="UPSTREAM_PERMANENT",
            failure_class=FailureClass.PERMANENT_UPSTREAM,
            severity=Severity.ERROR,
            detection="4xx from Sheets — bad key, sheet not shared, wrong id",
            user_response="Apologises for the system, offers a representative",
            recovery="Not retried; it cannot succeed. Needs a credential or sharing fix.",
        ),
        FailureMode(
            code="MALFORMED_DATA",
            failure_class=FailureClass.DATA_QUALITY,
            severity=Severity.ERROR,
            detection="Missing required column, empty sheet, or unreadable 200 body",
            user_response="Apologises for the system, offers a representative",
            recovery=(
                "Not retried. A malformed *row* is skipped and logged by position "
                "so the other callers are unaffected; a malformed *header* stops "
                "the operation, because 'no match' would be indistinguishable "
                "from 'unreadable'."
            ),
        ),
        FailureMode(
            code="INCOMPLETE_CLAIM_DATA",
            failure_class=FailureClass.DATA_QUALITY,
            severity=Severity.WARNING,
            detection="Status is DOCUMENTS_REQUIRED but no documents are listed",
            user_response=(
                "Admits the gap and offers a representative. Never names a plausible document set."
            ),
            recovery="Not retried. The sheet needs correcting.",
        ),
        # --- business outcomes ------------------------------------------------
        FailureMode(
            code="CUSTOMER_NOT_FOUND",
            failure_class=FailureClass.NOT_FOUND,
            severity=Severity.INFO,
            detection="Sheet read successfully; no row matches the normalised number",
            user_response="Offers another number or a representative",
            recovery=(
                "Caller may retry, up to three lookups. Costs no verification "
                "attempts — nothing was checked, so nothing was failed."
            ),
        ),
        FailureMode(
            code="CLAIM_NOT_FOUND",
            failure_class=FailureClass.NOT_FOUND,
            severity=Severity.INFO,
            detection="Sheet read successfully; no claim for this customer",
            user_response="Says so plainly and offers a representative",
            recovery="None needed; the answer is correct",
        ),
        FailureMode(
            code="FAQ_NO_ANSWER",
            failure_class=FailureClass.NOT_FOUND,
            severity=Severity.INFO,
            detection="Best match scores below the confidence threshold",
            user_response=(
                "States the limitation, lists what it can cover, offers a "
                "representative. Nothing is composed."
            ),
            recovery="Add a keyword to the knowledge file — content edit, no code change",
        ),
        # --- caller input -----------------------------------------------------
        FailureMode(
            code="INVALID_PHONE_NUMBER",
            failure_class=FailureClass.CALLER_INPUT,
            severity=Severity.INFO,
            detection="normalize_phone returns None",
            user_response="Asks for the number again, one digit at a time",
            recovery="Caller retries; distinct from 'no account with that number'",
        ),
        FailureMode(
            code="VERIFICATION_FAILED",
            failure_class=FailureClass.CALLER_INPUT,
            severity=Severity.INFO,
            detection="Constant-time comparison against the stored value fails",
            user_response="Says it does not match and offers another try",
            recovery="Up to three attempts; the third is terminal",
        ),
        FailureMode(
            code="ATTEMPTS_EXHAUSTED",
            failure_class=FailureClass.AUTHORIZATION,
            severity=Severity.WARNING,
            detection="Three failed verification attempts on one call",
            user_response="Explains it cannot verify them and offers a representative",
            recovery=(
                "Terminal for the call. A later correct answer is refused "
                "without being checked, so guessing cannot pay off."
            ),
        ),
        FailureMode(
            code="NOT_AUTHORIZED",
            failure_class=FailureClass.AUTHORIZATION,
            severity=Severity.WARNING,
            detection="Session is not AUTHENTICATED when claim access is attempted",
            user_response=(
                "One fixed line, identical for every unauthorised state, so probing reveals nothing"
            ),
            recovery="Complete verification. The repository is never reached.",
        ),
        # --- tool layer -------------------------------------------------------
        FailureMode(
            code="UNKNOWN_TOOL",
            failure_class=FailureClass.INTERNAL,
            severity=Severity.WARNING,
            detection="Tool name not in the registry",
            user_response="Apologises and offers a representative",
            recovery=(
                "Not retried. Means the assistant is configured with a tool this "
                "build does not implement."
            ),
        ),
        FailureMode(
            code="MISSING_ARGUMENTS",
            failure_class=FailureClass.INTERNAL,
            severity=Severity.WARNING,
            detection="A required parameter is absent or empty",
            user_response="Apologises and offers a representative",
            recovery="Not retried. The agent may call again with arguments.",
        ),
        FailureMode(
            code="ARGUMENTS_IGNORED",
            failure_class=FailureClass.INTERNAL,
            severity=Severity.WARNING,
            detection="Arguments supplied that no tool parameter declares",
            user_response=("None — the tool runs normally with the extra arguments dropped"),
            recovery=(
                "None needed. This is the guard that stops a forged "
                "`authenticated=true` reaching a handler."
            ),
        ),
        FailureMode(
            code="TOOL_FAILED",
            failure_class=FailureClass.INTERNAL,
            severity=Severity.ERROR,
            detection="A tool handler raised",
            user_response="Apologises and offers a representative",
            recovery="Not retried. A traceback goes to the log; the call continues.",
        ),
        FailureMode(
            code="RETRIEVAL_FAILED",
            failure_class=FailureClass.INTERNAL,
            severity=Severity.ERROR,
            detection="FAQ search itself raised",
            user_response=(
                "Says it cannot look that up and offers a representative — "
                "distinct from 'we don't cover that'"
            ),
            recovery="Not retried. Never falls back to what the model remembers.",
        ),
        # --- transport --------------------------------------------------------
        FailureMode(
            code="WEBHOOK_UNAUTHORIZED",
            failure_class=FailureClass.AUTHORIZATION,
            severity=Severity.WARNING,
            detection="Missing or wrong x-vapi-secret header",
            user_response="None — 401 before any work is done",
            recovery=(
                "Terse by design; checked before service availability so 401 and "
                "503 cannot be told apart."
            ),
        ),
        FailureMode(
            code="WEBHOOK_MALFORMED",
            failure_class=FailureClass.CALLER_INPUT,
            severity=Severity.WARNING,
            detection="Body is not JSON, or not a JSON object",
            user_response="None — 400, nothing to speak to",
            recovery="Not retried by us; Vapi may redeliver",
        ),
        FailureMode(
            code="WEBHOOK_HANDLER_FAILED",
            failure_class=FailureClass.INTERNAL,
            severity=Severity.ERROR,
            detection="An exception escaped the conversation service",
            user_response=(
                "200 with an empty acknowledgement. A 500 would make Vapi retry "
                "or drop the call; a caller must not lose a call to our bug."
            ),
            recovery="Traceback logged with the call id; the call continues",
        ),
        FailureMode(
            code="SERVICE_UNAVAILABLE",
            failure_class=FailureClass.CONFIGURATION,
            severity=Severity.ERROR,
            detection="Data integration not configured at startup",
            user_response="None — 503 to the platform",
            recovery=(
                "Deliberate: the process still starts and /health stays green, so "
                "a credential problem is a visible unavailable service rather "
                "than a crash loop."
            ),
        ),
        # --- post-call --------------------------------------------------------
        FailureMode(
            code="POSTCALL_FAILED",
            failure_class=FailureClass.TRANSIENT_UPSTREAM,
            severity=Severity.ERROR,
            detection="Interactions sheet write failed or its response was unreadable",
            user_response="None — the caller has already hung up",
            recovery=(
                "Retried within the client's budget, then logged in full. The "
                "record is recoverable from the logs but not automatically "
                "refiled — see DEFERRED 8.3."
            ),
            retried=True,
        ),
        FailureMode(
            code="POSTCALL_DUPLICATE",
            failure_class=FailureClass.NOT_FOUND,
            severity=Severity.INFO,
            detection="call_id already present in memory or on the sheet",
            user_response="None",
            recovery=(
                "Expected, not an error: it is the correct answer to a "
                "redelivered webhook. No second row is written."
            ),
        ),
        FailureMode(
            code="GUIDANCE_CONFIGURATION_ERROR",
            failure_class=FailureClass.CONFIGURATION,
            severity=Severity.ERROR,
            detection="Claim guidance missing, unreadable, or missing a status",
            user_response="None — the service refuses to start",
            recovery=(
                "Deliberately fatal. The alternative is an agent with nothing "
                "configured to say, improvising mid-call."
            ),
        ),
        FailureMode(
            code="FAQ_CONFIGURATION_ERROR",
            failure_class=FailureClass.CONFIGURATION,
            severity=Severity.ERROR,
            detection="A required FAQ topic is missing or a file is malformed",
            user_response="None — the service refuses to start",
            recovery="Deliberately fatal, for the same reason",
        ),
        FailureMode(
            code="PROMPT_CONFIGURATION_ERROR",
            failure_class=FailureClass.CONFIGURATION,
            severity=Severity.ERROR,
            detection="Agent prompt file missing or empty",
            user_response="None — the service refuses to start",
            recovery="Deliberately fatal",
        ),
        FailureMode(
            code="VALIDATION_ERROR",
            failure_class=FailureClass.CALLER_INPUT,
            severity=Severity.WARNING,
            detection="Request body fails schema validation",
            user_response="None — 422 with field names but never submitted values",
            recovery="Caller-side fix",
        ),
        FailureMode(
            code="NOT_FOUND",
            failure_class=FailureClass.NOT_FOUND,
            severity=Severity.WARNING,
            detection="Unknown HTTP route",
            user_response="None — 404 in the standard envelope",
            recovery="None needed",
        ),
        FailureMode(
            code="INTERNAL_ERROR",
            failure_class=FailureClass.INTERNAL,
            severity=Severity.ERROR,
            detection="Unhandled exception reaching the outermost handler",
            user_response=("Generic apology with a request id. No stack trace, no internals."),
            recovery="Traceback logged with the request id",
        ),
    )
}


def classify(code: str) -> FailureMode | None:
    """Look up a failure by its stable code."""
    return FAILURE_CATALOGUE.get(code)


def is_infrastructure_failure(code: str) -> bool:
    """Whether this code means *we* failed, rather than 'no such record'.

    The check that keeps a timeout from ever being reported as a missing
    customer.
    """
    mode = classify(code)
    return mode is not None and mode.is_infrastructure
