# Requirements checklist

Every requirement in [CLAUDE.md](../CLAUDE.md), checked against the
implementation. Section numbers match.

Verified at Phase 9 against **718 passing tests** and the eight end-to-end
scenario transcripts in
[`test_end_to_end_scenarios.py`](../backend/tests/test_end_to_end_scenarios.py).

Legend: **✅** done and tested · **⚠️** done with a stated limitation · **❌** not done

---

## §2 Assignment requirements

| Requirement | | Evidence |
| --- | --- | --- |
| Greet the caller | ✅ | `firstMessage` in the assistant config; prompt §Greeting |
| Ask for the phone number | ✅ | `lookup_customer`; scenario 1 |
| Look up via an external system | ✅ | Google Sheets, Integration #1 |
| Confirm identity before proceeding | ✅ | `verify_identity`; `require_authenticated` gates claim access |
| Retrieve and communicate claim status | ✅ | `get_claim_status` + `services/voice.py` |
| Explain document submission | ✅ | Scenario 5; configured, never composed |
| FAQ: hours, address, new claim, process | ✅ | `knowledge/*.md`; scenario 6 |
| Representative requests | ✅ | Scenario 4 |
| Unsupported questions | ✅ | Scenario 7 |
| Emergency situations | ✅ | Scenario 8 |
| Post-call record: name, summary, sentiment, timestamp | ✅ | Integration #2 |

## §3 Mandatory demo scenarios

| | | Evidence |
| --- | --- | --- |
| Happy path | ✅ | `test_scenario_1_happy_path` |
| Authentication failure | ✅ | `test_scenario_2_authentication_failure` |
| Customer not found | ✅ | `test_scenario_3_customer_not_found` |
| Representative escalation | ✅ | `test_scenario_4_representative_escalation` |
| Documents required | ✅ | `test_scenario_5_documents_required` |

## §4 Bonus features

| | | Notes |
| --- | --- | --- |
| Knowledge base integration | ✅ | Per-topic files with scored retrieval and a confidence band |
| Multi-agent orchestration | ✅ | Supervisor + three specialists over the *same* registry, so no business logic is duplicated. Routing is deterministic rather than a second model call — see the README. The layer cannot authenticate anyone and is removable with one argument. |
| Additional backend integrations | ❌ | Two integrations, as specified. |

## §5 Architecture

| | | |
| --- | --- | --- |
| Caller → platform → agent → tools → services → external | ✅ | See [architecture.md](architecture.md) |
| Platform owns voice, backend owns logic | ✅ | All Vapi code in `integrations/voice_platform.py` |
| Business logic not in prompt text | ✅ | The prompt names tools and tone. It cannot authenticate anyone, and has no claim data or FAQ content in it. |

## §6 Conversation state

| | | |
| --- | --- | --- |
| All ten required fields | ✅ | `SessionState`; verified by field-list assertion |
| Authentication state explicit | ✅ | `AuthenticationStatus`, five states |
| Never inferred from model belief | ✅ | Frozen dataclass; only repository results move it |

## §7 Authentication security

| | | |
| --- | --- | --- |
| No claim id / status / details / documents pre-auth | ✅ | `test_preauth_disclosure.py`; `ClaimStatusView` unreachable until AUTHENTICATED |
| No sensitive customer information pre-auth | ✅ | Only the first name is disclosed, for the greeting |
| The five listed bypass phrases fail | ✅ | Run through every caller input in `test_voice_security.py` (43 tests) |
| Application state, not model belief, authorises | ✅ | `require_authenticated(session)` — one gate, no override parameter |

## §8 Tools

| | | |
| --- | --- | --- |
| Six required logical tools | ⚠️ | Five exposed to the agent. `complete_call` is **deliberately not** a tool: completion is driven by the platform's `end-of-call-report`, not by the model deciding a call is over. The capability exists and runs on every call. |
| No generic database/API/code tools | ✅ | Asserted by name in `test_conversation_service.py` |
| Validate inputs | ✅ | Registry drops undeclared arguments and rejects missing required ones |
| Structured outputs | ✅ | `ToolResult` + typed payload |
| Handle failures | ✅ | Failures are returned, never raised, so a call survives them |
| Avoid leaking sensitive data | ✅ | `data` is None on every non-success outcome |
| Log execution safely | ✅ | `tool.invoked` / `tool.completed` log names and outcomes, never values |
| Clear authorization boundaries | ✅ | One gate, checked before the repository is touched |

## §9 Claim access

| | | |
| --- | --- | --- |
| Requires an authenticated session | ✅ | `test_claim_authorization.py` |
| Not invocable through prompt manipulation | ✅ | `get_claim_status` takes no identity argument; a supplied `customer_id` is checked and refused on mismatch, before any lookup |
| Data retrieved, not invented | ✅ | Repository-sourced; nothing composes a status |

## §10 Customer lookup

| | | |
| --- | --- | --- |
| External system | ✅ | Google Sheets |
| Normalise phone numbers | ✅ | `core/phone.py`, both sides of the match |
| CUSTOMER_FOUND / NOT_FOUND / INTEGRATION_ERROR distinguished | ✅ | `CustomerLookupOutcome` |
| Integration error never reported as not-found | ✅ | Asserted directly in `test_customer_repository.py` |

## §11 Claim status

| | | |
| --- | --- | --- |
| All five statuses | ✅ | `ClaimStatus` |
| Identify missing documents | ✅ | Scenario 5 |
| Explain the next action | ✅ | From `claim_guidance.json` |
| Do not invent submission procedures | ✅ | No code path composes one; startup fails if a status is unconfigured |

## §12 FAQ knowledge

| | | |
| --- | --- | --- |
| Separated from the system prompt | ✅ | `knowledge/*.md` |
| Four supported topics | ✅ | Plus document submission |
| No hallucinated answers | ✅ | Deterministic retrieval; no answer means no answer |
| Offer a representative when uncertain | ✅ | Scenario 7 |

## §13 Representative escalation

| | | |
| --- | --- | --- |
| Available at any point | ✅ | Scenario 4, including mid-authentication |
| No forced workflow first | ✅ | Asserted: the response mentions no verification step |
| Structured record (id, call, customer, reason, timestamp, status) | ✅ | `EscalationRecord` |
| Voice transfer if supported, else realistic workflow | ⚠️ | Implemented behind `VOICE_TRANSFER_PHONE_NUMBER`; **unverified against a live Vapi account** (no destination to test against). Unset, the escalation workflow runs and is fully tested. |

## §14 Emergency handling

| | | |
| --- | --- | --- |
| Treated as safety-sensitive | ✅ | Logged at ERROR; two independent detectors |
| Prioritise safety | ✅ | The requested tool does not run |
| Advise the appropriate emergency service | ✅ | Fixed 911 response |
| Do not pretend to provide assistance | ✅ | "they can help in a way I can't" |
| Do not continue claims troubleshooting | ✅ | Asserted against claims vocabulary |

## §15 Unsupported questions

| | | |
| --- | --- | --- |
| Do not hallucinate | ✅ | `data` is None; fixed refusal wording |
| Communicate the limitation | ✅ | Scenario 7 |
| Offer a representative | ✅ | Scenario 7 |

## §16 Voice UX

| | | |
| --- | --- | --- |
| Calm, supportive, concise, natural | ✅ | See the scenario transcripts |
| Short responses | ✅ | Asserted ≤ 60 words |
| One question at a time | ✅ | Asserted ≤ 1 "?" per turn |
| No markdown / JSON / jargon | ✅ | Asserted per answer |
| No repeated greeting or re-authentication | ✅ | `ALREADY_AUTHENTICATED` short-circuits |
| Never promise approval, timing or outcome | ✅ | Asserted against a promise vocabulary |

## §17 Post-call processing

| | | |
| --- | --- | --- |
| Record on every completed call | ✅ | Including calls where nothing happened |
| caller_name, call_summary, sentiment, timestamp | ✅ | Required fields asserted |
| The eight useful additional fields | ✅ | 12 columns total |
| Controlled sentiment values | ✅ | `Sentiment` enum only |
| `call_id` as idempotency key | ✅ | In-process **and** against the sheet |
| Summary of the actual interaction | ⚠️ | Derived from observed state, not a transcript, so it cannot invent — but it is plainer than a model would write. **Sentiment is outcome-derived, not tone analysis**; documented in the README so the column is not over-read. |

## §18 Integrations

| | | |
| --- | --- | --- |
| #1 customer and claim retrieval | ✅ | API key, read-only |
| #2 post-call persistence | ✅ | Service account, separate spreadsheet |
| Behind interfaces | ✅ | Four repository protocols |
| Not coupled to the Sheets API | ✅ | No service, tool or agent imports anything Sheets-shaped |

## §19 Error handling

| | | |
| --- | --- | --- |
| Failures do not crash the conversation | ✅ | Webhook returns 200 even when handling fails |
| Timeouts | ✅ | `IntegrationTimeoutError`, tested |
| Rate limits | ✅ | 429 retried |
| Malformed external data | ✅ | Bad rows skipped, bad headers raise |
| Customer / claim not found | ✅ | Distinct outcomes |
| Authentication failure | ✅ | Bounded at three |
| FAQ failure | ✅ | Distinct from no-answer |
| Post-call persistence failure | ✅ | Cannot reach a caller |
| Bounded retries, backoff with jitter | ✅ | `core/retry.py` |

## §20 Observability

| | | |
| --- | --- | --- |
| All ten required events | ✅ | Verified present by name |
| call_id, event, duration, success | ✅ | `call_id` bound in contextvars for the whole event |
| No unnecessary sensitive logging | ✅ | Phone redacted at the formatter; httpx URL logging silenced so the API key cannot leak |

## §21 Testing

| | | |
| --- | --- | --- |
| All ten required areas | ✅ | Each has a named test |
| Deterministic business logic | ✅ | No randomness outside retry jitter, which is injected |
| External services mocked | ✅ | No credentials, no network, anywhere in the suite |

## §22 Development rules

| | | |
| --- | --- | --- |
| Inspect before modifying | ✅ | Each phase began with inspection |
| Preserve working functionality | ✅ | 718 tests, run every phase |
| Small cohesive changes | ✅ | One commit per phase |
| No dependency without a reason | ✅ | Four runtime deps: fastapi, uvicorn, pydantic(-settings), httpx, google-auth — the last two each justified in `pyproject.toml` |
| Bonuses after mandatory work | ✅ | Knowledge base came after all mandatory phases |

## §23 Production mindset

| | | |
| --- | --- | --- |
| Timeouts, bounded retries, structured logs | ✅ | |
| Clear error handling | ✅ | One envelope, one handler set |
| Stateless backend where possible | ⚠️ | Sessions are process-local by design. `SessionStore` is a protocol so Redis is a swap — but adding a datastore this assignment does not need would be the distributed infrastructure §23 warns against. Blocks multi-replica deployment; tracked as [DEFERRED](DEFERRED.md) 3.1. |
| Secure secrets | ✅ | Environment-only; production refuses to start without the webhook secret |
| Environment-based configuration | ✅ | One module reads the environment |
| Health checks | ✅ | `/health` and `/ready`, correctly separated |
| Graceful degradation | ✅ | Runs without either integration configured |
| No unnecessary distributed infrastructure | ✅ | None added |

## §24 Definition of done

| | | |
| --- | --- | --- |
| A real voice call can be demonstrated | ✅ | Verified by voice against a Vapi assistant over a public tunnel. Every mandatory scenario walked, with the backend's telemetry as evidence — see [CORE-COMPLETE.md](CORE-COMPLETE.md#live-verification). |
| Customer lookup works | ✅ | |
| Authentication works | ✅ | |
| Claim lookup works | ✅ | |
| Documents-required scenario works | ✅ | |
| FAQ works | ✅ | |
| Representative escalation works | ✅ | |
| Emergency handling works | ✅ | |
| Unsupported questions have a safe fallback | ✅ | |
| Post-call data written externally | ✅ | Verified against a stub performing a real OAuth exchange |
| All four mandatory demo scenarios work | ✅ | |
| Tests pass | ✅ | 718 |
| README explains how to run it | ✅ | |
| Explicable in an interview | ✅ | [architecture.md](architecture.md) records why, not just what |

---

## Honest summary

**Not done:** nothing mandatory. Both bonus features — knowledge base and
multi-agent orchestration — are implemented.

**Done with a stated limitation:** `complete_call` is platform-driven rather
than a model tool (§8); voice transfer is implemented but unverified (§13);
summary and sentiment are derived from state rather than a transcript (§17);
sessions are process-local (§23).

Every one of those is recorded with its reasoning in
[DEFERRED.md](DEFERRED.md). Nothing on this page is claimed without a test or a
transcript behind it.
