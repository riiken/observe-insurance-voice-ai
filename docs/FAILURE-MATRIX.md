# Failure matrix

**Generated from [`app/core/failures.py`](../backend/app/core/failures.py) — do not edit by hand.** Regenerate with `python scripts/generate_failure_matrix.py`.

A test asserts that every error code the system defines appears in the catalogue, so a new failure mode cannot ship without a decision about how it is handled.

The distinction the whole matrix is arranged around: **an infrastructure failure is never a business outcome.** A Google Sheets timeout is not a customer who does not exist, and the two never share a classification, a log level, or a sentence to a caller.

## Transient Upstream

A dependency is briefly unwell. Retry.

| Failure | Detection | User response | Recovery | Retried | Logged |
| ------- | --------- | ------------- | -------- | ------- | ------ |
| `INTEGRATION_ERROR` | 429 / 5xx / connection failure from Sheets | Apologises for the system, offers a representative | Retried when the status is retryable; otherwise surfaced at once | yes | ERROR |
| `INTEGRATION_TIMEOUT` | httpx.TimeoutException in the Sheets client | Apologises for the system, offers a representative. Never 'we have no record of you'. | Retried with jittered backoff inside the turn's time budget | yes | ERROR |
| `POSTCALL_FAILED` | Interactions sheet write failed or its response was unreadable | None — the caller has already hung up | Retried within the client's budget, then logged in full. The record is recoverable from the logs but not automatically refiled — see DEFERRED 8.3. | yes | ERROR |

## Permanent Upstream

A dependency will keep saying no. Do not retry — a human must change something.

| Failure | Detection | User response | Recovery | Retried | Logged |
| ------- | --------- | ------------- | -------- | ------- | ------ |
| `UPSTREAM_PERMANENT` | 4xx from Sheets — bad key, sheet not shared, wrong id | Apologises for the system, offers a representative | Not retried; it cannot succeed. Needs a credential or sharing fix. | no | ERROR |

## Data Quality

The data we were given is unusable. Our problem, never reported as 'no such record'.

| Failure | Detection | User response | Recovery | Retried | Logged |
| ------- | --------- | ------------- | -------- | ------- | ------ |
| `INCOMPLETE_CLAIM_DATA` | Status is DOCUMENTS_REQUIRED but no documents are listed | Admits the gap and offers a representative. Never names a plausible document set. | Not retried. The sheet needs correcting. | no | WARNING |
| `MALFORMED_DATA` | Missing required column, empty sheet, or unreadable 200 body | Apologises for the system, offers a representative | Not retried. A malformed *row* is skipped and logged by position so the other callers are unaffected; a malformed *header* stops the operation, because 'no match' would be indistinguishable from 'unreadable'. | no | ERROR |

## Not Found

The dependency answered correctly, and the answer is that there is no such record. **Not a failure.**

| Failure | Detection | User response | Recovery | Retried | Logged |
| ------- | --------- | ------------- | -------- | ------- | ------ |
| `CLAIM_NOT_FOUND` | Sheet read successfully; no claim for this customer | Says so plainly and offers a representative | None needed; the answer is correct | no | INFO |
| `CUSTOMER_NOT_FOUND` | Sheet read successfully; no row matches the normalised number | Offers another number or a representative | Caller may retry, up to three lookups. Costs no verification attempts — nothing was checked, so nothing was failed. | no | INFO |
| `FAQ_NO_ANSWER` | Best match scores below the confidence threshold | States the limitation, lists what it can cover, offers a representative. Nothing is composed. | Add a keyword to the knowledge file — content edit, no code change | no | INFO |
| `NOT_FOUND` | Unknown HTTP route | None — 404 in the standard envelope | None needed | no | WARNING |
| `POSTCALL_DUPLICATE` | call_id already present in memory or on the sheet | None | Expected, not an error: it is the correct answer to a redelivered webhook. No second row is written. | no | INFO |

## Caller Input

The caller gave something we could not use. Normal.

| Failure | Detection | User response | Recovery | Retried | Logged |
| ------- | --------- | ------------- | -------- | ------- | ------ |
| `INVALID_PHONE_NUMBER` | normalize_phone returns None | Asks for the number again, one digit at a time | Caller retries; distinct from 'no account with that number' | no | INFO |
| `VALIDATION_ERROR` | Request body fails schema validation | None — 422 with field names but never submitted values | Caller-side fix | no | WARNING |
| `VERIFICATION_FAILED` | Constant-time comparison against the stored value fails | Says it does not match and offers another try | Up to three attempts; the third is terminal | no | INFO |
| `WEBHOOK_MALFORMED` | Body is not JSON, or not a JSON object | None — 400, nothing to speak to | Not retried by us; Vapi may redeliver | no | WARNING |

## Authorization

The session does not permit this.

| Failure | Detection | User response | Recovery | Retried | Logged |
| ------- | --------- | ------------- | -------- | ------- | ------ |
| `ATTEMPTS_EXHAUSTED` | Three failed verification attempts on one call | Explains it cannot verify them and offers a representative | Terminal for the call. A later correct answer is refused without being checked, so guessing cannot pay off. | no | WARNING |
| `NOT_AUTHORIZED` | Session is not AUTHENTICATED when claim access is attempted | One fixed line, identical for every unauthorised state, so probing reveals nothing | Complete verification. The repository is never reached. | no | WARNING |
| `WEBHOOK_UNAUTHORIZED` | Missing or wrong x-vapi-secret header | None — 401 before any work is done | Terse by design; checked before service availability so 401 and 503 cannot be told apart. | no | WARNING |

## Configuration

This deployment is set up wrongly.

| Failure | Detection | User response | Recovery | Retried | Logged |
| ------- | --------- | ------------- | -------- | ------- | ------ |
| `FAQ_CONFIGURATION_ERROR` | A required FAQ topic is missing or a file is malformed | None — the service refuses to start | Deliberately fatal, for the same reason | no | ERROR |
| `GUIDANCE_CONFIGURATION_ERROR` | Claim guidance missing, unreadable, or missing a status | None — the service refuses to start | Deliberately fatal. The alternative is an agent with nothing configured to say, improvising mid-call. | no | ERROR |
| `PROMPT_CONFIGURATION_ERROR` | Agent prompt file missing or empty | None — the service refuses to start | Deliberately fatal | no | ERROR |
| `SERVICE_UNAVAILABLE` | Data integration not configured at startup | None — 503 to the platform | Deliberate: the process still starts and /health stays green, so a credential problem is a visible unavailable service rather than a crash loop. | no | ERROR |

## Internal

Our bug.

| Failure | Detection | User response | Recovery | Retried | Logged |
| ------- | --------- | ------------- | -------- | ------- | ------ |
| `ARGUMENTS_IGNORED` | Arguments supplied that no tool parameter declares | None — the tool runs normally with the extra arguments dropped | None needed. This is the guard that stops a forged `authenticated=true` reaching a handler. | no | WARNING |
| `INTERNAL_ERROR` | Unhandled exception reaching the outermost handler | Generic apology with a request id. No stack trace, no internals. | Traceback logged with the request id | no | ERROR |
| `MISSING_ARGUMENTS` | A required parameter is absent or empty | Apologises and offers a representative | Not retried. The agent may call again with arguments. | no | WARNING |
| `RETRIEVAL_FAILED` | FAQ search itself raised | Says it cannot look that up and offers a representative — distinct from 'we don't cover that' | Not retried. Never falls back to what the model remembers. | no | ERROR |
| `TOOL_FAILED` | A tool handler raised | Apologises and offers a representative | Not retried. A traceback goes to the log; the call continues. | no | ERROR |
| `UNKNOWN_TOOL` | Tool name not in the registry | Apologises and offers a representative | Not retried. Means the assistant is configured with a tool this build does not implement. | no | WARNING |
| `WEBHOOK_HANDLER_FAILED` | An exception escaped the conversation service | 200 with an empty acknowledgement. A 500 would make Vapi retry or drop the call; a caller must not lose a call to our bug. | Traceback logged with the call id; the call continues | no | ERROR |

---

## Budgets

| | Value | Why |
| --- | --- | --- |
| Per-attempt timeout, in call | `HTTP_TIMEOUT_SECONDS` (10s) | One Sheets read |
| **Total budget, in call** | `VOICE_TURN_BUDGET_SECONDS` (6s) | A caller is listening to silence. Three attempts at ten seconds is thirty seconds of nothing, by which point retrying is pointless — they have gone. Failing fast leaves time to apologise and offer a person. |
| Per-attempt timeout, post-call | `POSTCALL_TIMEOUT_SECONDS` (20s) | Nobody is waiting, so it may take longer |
| Attempts | `HTTP_MAX_RETRIES` (2) | Bounded |
| Backoff | `HTTP_BACKOFF_BASE_SECONDS` (0.2s), exponential, **full jitter**, capped at 5s | Without jitter, every concurrent call hitting the same rate limit retries in lockstep and recreates the burst |

## What a caller never hears

Asserted for every failure branch in [`test_failure_handling.py`](../backend/tests/test_failure_handling.py):

- HTTP status codes, ours or an upstream's
- The words *Google*, *sheet*, *spreadsheet*, or any library name
- Stack traces, exception names, `None`, or JSON punctuation
- Which specific check refused them — every authorization refusal is worded identically, so probing reveals nothing
