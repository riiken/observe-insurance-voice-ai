# Security review

Against [CLAUDE.md](../CLAUDE.md) §7, §8, §9, §20 and §23. Every claim here has
a test behind it; where something is a residual risk it is named as one rather
than left out.

---

## 1. Secrets

| | Status | |
| --- | --- | --- |
| Secrets in source or git | ✅ none | `.env` is git-ignored; only `.env.example` is committed, with empty values |
| Secrets in the image | ✅ none | The Dockerfile copies code and knowledge, never `.env` |
| Secrets in `repr` / tracebacks | ✅ masked | `SecretStr` for the API key, webhook secret and service-account key. **This was a real hole:** before, `repr(settings)` printed all three in full, and a pydantic validation error or a traceback frame would have carried them into a log store. |
| Secrets in logs | ✅ none | httpx logs request URLs at INFO, and the Sheets key travels in the query string — that logger is silenced to WARNING, with a test asserting it |
| Secrets in `/metrics` | ✅ none | Asserted: the endpoint contains no identifiers or credentials |
| Production without a webhook secret | ✅ refuses to start | An unauthenticated webhook is an open door to the tool layer |

**Residual:** the service-account key is an environment variable. Correct for a
container platform's secret store, which is how this deploys; a secrets manager
is deployment work ([DEFERRED](DEFERRED.md) 8.5).

## 2. Environment variables

Read in exactly one module, [`core/config.py`](../backend/app/core/config.py) —
nothing else touches `os.environ`. Invalid values fail at startup rather than
mid-call. Secrets are `SecretStr`; everything else is plain and safe to log.

## 3. Logs

| | |
| --- | --- |
| Verification value | Never logged. Asserted over a whole call. |
| Full phone number | Never logged. On a *failed* lookup the last four digits appear (`***9999`) because "which number did not match" is the first question anyone asks; on success the number is not logged at all, since `customer_id` is the better key. |
| Customer name | Never logged. An id joins to the sheet; a name is PII sitting in a log store. |
| Claim details | Only `claim_id` and status, and only after authentication. |
| Escalation notes | Not logged — they are model-written free text and could contain anything. |
| Caller utterances | Never logged, including by the emergency detector, which logs *which rules matched* and not the sentence. |

Every domain event carries `call_id`, bound in a contextvar for the whole
webhook event, so one filter reconstructs a call. Asserted.

## 4. The authentication boundary

- `SessionState` is a **frozen** dataclass. `session.authentication_status = AUTHENTICATED` raises. Every transition requires a real repository result.
- The session **never crosses the wire**: server-side, keyed by `call_id`, and nothing from a webhook payload is deserialised into it.
- One gate, `require_authenticated(session)`, with no override parameter, checked **before** the repository is touched — so an unauthorised request causes no lookup at all.
- Every refusal is worded identically, so probing reveals nothing about which check failed.
- Three attempts, terminal. A correct answer afterwards is refused **without being checked**, so guessing cannot pay off.
- The session is released at call end, so a `call_id` cannot be reused.

## 5. Tool authorization

The exposed surface is five narrow operations. There is no generic query tool,
no raw API access, no escape hatch — asserted by name.

- `call_id` is **not a tool parameter**. It comes from the platform payload and is injected by the dispatcher, so the model cannot name another call and inherit its authentication.
- The registry **drops any argument no tool declares**. A forged `authenticated`, `skip_auth`, `override` or `customer_id` is logged and discarded before a handler runs.
- `get_claim_status` takes no identity argument at all. A supplied `customer_id` is compared against the session's and refused on mismatch, before any lookup.
- A tool that raises returns a spoken apology; the traceback goes to the log, never to the caller.

## 6. Prompt injection

The defence is structural, not instructional — there is nowhere to inject.

The CLAUDE.md §7 phrases are run through **every** caller-facing input and every
forged-argument shape (`test_voice_security.py`, 43 tests). They are treated as
what they are: wrong answers, which cost an attempt.

The prompt itself carries tone and tool choice but no business rule. It cannot
authenticate anyone, and contains no claim data or FAQ content, so rewriting it
— or arguing with it — changes nothing that matters.

**Residual:** a compromised model could call `request_representative`
repeatedly, or spam `search_faq`. Bounded by call duration and rate-limited by
nothing ([DEFERRED](DEFERRED.md) 5.5).

## 7. Sensitive data exposure

| Surface | |
| --- | --- |
| Pre-authentication | Only the caller's **first name**, for the greeting. Asserted against the whole authentication result. |
| `Customer` model | Has **no field** for the verification value. A secret that does not exist in a type cannot leak. |
| Escalation record | Carries no claim data — an unverified caller can raise one. |
| Error responses | One envelope; `AppError.context` is logged and never serialised. Upstream statuses, spreadsheet ids and stack traces stay server-side. |
| `/metrics` | Counts and durations only. |
| `/docs`, `/openapi.json` | Disabled in production. |
| Failure speech | Asserted free of status codes, vendor names, stack traces, `None` and JSON punctuation. |

**Residual:** the Sheets read credential is an API key, which requires the
customer sheet to be **link-shared**. Fine for the synthetic demo data and
**not acceptable for real records** — a service account is the production path
([DEFERRED](DEFERRED.md) 2.1, now implemented for writes). The write credential
already has its own spreadsheet so it cannot edit customer data.

---

## Summary

Nothing outstanding that a caller could exploit. The residual risks are
deployment-shaped — link-shared read sheet, env-var secret, no rate limiting —
and each is recorded with its reasoning in [DEFERRED.md](DEFERRED.md).

The one real hole found in this review was secrets appearing in
`repr(Settings)`. It is fixed and tested.
