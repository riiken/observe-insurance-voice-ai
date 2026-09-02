# Core complete — mandatory functionality gate

Verification of all 25 mandatory requirements from the Observe Insurance
assessment. No new features; defects found were fixed.

| Gate | Result |
| ---- | ------ |
| `ruff check` | **PASS** |
| `ruff format --check` | **PASS** (111 files) |
| `mypy` (app/) | **PASS** (73 files) |
| `pytest` | **PASS** — 843 tests, deterministic, no credentials, no network |
| Docker build + run, fully configured | **PASS** — healthy, `/ready` green on all three dependencies |
| Full call through the container | **PASS** — real OAuth exchange, row written to the external sheet |

---

## Verification table

| # | Requirement | Implementation | Test / demo |
| - | ----------- | -------------- | ----------- |
| 1 | Greeting | `firstMessage` + prompt §Greeting, served by `/api/v1/voice/assistant-config` | `test_the_assistant_config_carries_the_greeting` |
| 2 | Phone number collection | `lookup_customer` tool; `core/phone.py` normalises to E.164 | `test_phone.py` (11 spoken forms), `test_scenario_1_happy_path` |
| 3 | Customer lookup | `GoogleSheetsCustomerRepository.lookup_customer_by_phone` | `test_customer_repository.py` (30) |
| 4 | Identity confirmation | `verify_identity` → `AuthenticationService.submit_verification`; constant-time compare | `test_authentication_service.py` (30) |
| 5 | Claim status retrieval | `get_claim_status` → `ClaimsService`, gated by `require_authenticated` | `test_claim_status_tool.py` (40) |
| 6 | Claim status communication | `services/voice.py` renders speech from structured data | `test_voice.py` (46) |
| 7 | Documentation instructions | `claim_guidance.json` submission block; claim number read back | `test_scenario_5_documents_required` |
| 8 | Office hours FAQ | `knowledge/office_hours.md` | `test_scenario_6_faq[office hours]` |
| 9 | Mailing address FAQ | `knowledge/mailing_address.md` | `test_scenario_6_faq[mailing address]` |
| 10 | New claim FAQ | `knowledge/new_claim.md` | `test_scenario_6_faq[new claim]` |
| 11 | General claims process FAQ | `knowledge/claims_process.md` | `test_scenario_6_faq[claims process]` |
| 12 | Representative request | `request_representative` → `EscalationService` | `test_safety_and_escalation.py` |
| 13 | Unsupported question | `search_faq` confidence floor; fixed refusal, never composed | `test_scenario_7_unsupported_question` (5) |
| 14 | Emergency handling | Two detectors: prompt + `services/safety.py` at the dispatcher | `test_scenario_8_emergency`, 22 safety tests |
| 15 | Post-call record | `PostCallService` → `GoogleSheetsInteractionRepository` | `test_postcall.py` (47) |
| 16 | Caller name | `InteractionRecord.caller_name`, placeholder when unidentified | `test_the_record_carries_every_required_field` |
| 17 | Summary | `services/summary.py`, derived from observed state | `test_the_summary_describes_what_actually_happened` |
| 18 | Sentiment | `Sentiment` enum only, outcome-derived | `test_sentiment_only_ever_uses_the_controlled_vocabulary` |
| 19 | Timestamp | Timezone-aware UTC on every record | `test_the_timestamp_is_timezone_aware` |
| 20 | External read integration | Integration #1 — Sheets, API key, read-only | `test_customer_repository.py` + `test_claims_repository.py` (43) |
| 21 | External write integration | Integration #2 — separate sheet, service account | `test_postcall.py`; verified in-container against a real OAuth exchange |
| 22 | **Happy path** | Full flow | `test_scenario_1_happy_path` |
| 23 | **Authentication failure** | Three attempts, terminal, no claim data | `test_scenario_2_authentication_failure` |
| 24 | **Customer not found** | Distinct from failure; retry works | `test_scenario_3_customer_not_found` |
| 25 | **Representative escalation** | Any point, structured record | `test_scenario_4_representative_escalation` |

---

## Defects found and fixed by this gate

**1. The container could not start with the integration configured.** Content
paths were resolved as `parents[3]`, which assumes the repository layout; in the
image the app sits at `/srv/app`, so the FAQ directory resolved to `/knowledge`
and startup failed. The Dockerfile papered over it for the claim guidance with
an env var and not for the FAQ.

It went unnoticed because every previous container smoke test ran *unconfigured*
— and without an integration, no service layer is built and no content is
loaded. The image had been broken since Phase 6.

Fixed by discovering the directory rather than assuming it
(`core/paths.py`), removing the compensating env var, and adding two regression
tests plus an in-container run with the integration configured.

**2. Type checking was never configured**, so the codebase's annotations were
documentation rather than a guarantee. Running `mypy` found 12 issues: one
latent fragility (a narrowing on `customer_id` lost across a reassignment,
safe today and fragile to any change in that transition), loose annotations in
the webhook parser — the one place that handles hostile input — and known
Starlette stub noise. All fixed; `mypy` is now in the dev extras and configured
on `app/`.

---

## Live verification

The gate above runs offline. Everything below was then confirmed by **talking to
the assistant over a phone line** — a Vapi assistant on Claude Sonnet 5, reaching
this backend through a public tunnel, reading real rows from Google Sheets.

The evidence is the backend's own telemetry, taken from `/metrics` after the
session:

| Scenario | What the counters showed |
| -------- | ------------------------ |
| Happy path | `calls_completed{RESOLVED}`, `lookup_customer`/`verify_identity`/`get_claim_status` all SUCCESS |
| Authentication failure | `verify_identity` INVALID_INPUT ×2 then EXHAUSTED, `escalations{AUTHENTICATION_FAILED}` |
| Customer not found | `customer_lookups{CUSTOMER_NOT_FOUND}`, `escalations{CUSTOMER_NOT_FOUND}` — never an auth failure |
| Representative escalation | `request_representative` SUCCESS, no verification demanded first |
| Emergency | `escalations{EMERGENCY}` — and an ordinary "fire last month" claim did **not** trigger it |
| FAQ | `search_faq` SUCCESS, answered without verification |
| Post-call record | `postcall{persisted}` — rows written to a separate Interactions spreadsheet |

Tool latency ran 410–740 ms, so the model was never waiting on the backend.

Two things worth noting from the run:

**Integration #2 uses a second spreadsheet, not a second tab.** It was
initially pointed at the customer file, which the service warns about at
startup (`integration.shared_spreadsheet`). The write credential is the only
one in the system with write scope; on a shared file it could edit the
`verification_value` column that authenticates callers. Separating the files
closes that.

**The assistant cannot end a call.** No hang-up tool is attached, deliberately:
an agent that can terminate a call can terminate one during an emergency, and
the emergency response asks the *caller* to hang up and dial 911. Vapi's
silence timeout closes idle calls instead.

## Status

**Core complete.** All 25 mandatory requirements implemented, verified offline
by 843 tests and four gates, and demonstrated live by voice.

Remaining limitations are recorded with their reasoning in
[DEFERRED.md](DEFERRED.md) and summarised in
[REQUIREMENTS-CHECKLIST.md](REQUIREMENTS-CHECKLIST.md).
