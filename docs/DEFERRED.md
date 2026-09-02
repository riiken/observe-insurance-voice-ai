# Deferred work

Running ledger of everything consciously left undone, and where it will land.
Updated at the end of every phase: items are added when a phase defers them and
struck off when a later phase picks them up.

Anything here is a **decision**, not an oversight. If it is not written down, it
was not deferred — it was missed.

---

## Open

### From Phase 1 — foundation

| # | Item | Why deferred | Lands in |
| - | ---- | ------------ | -------- |
| 1.2 | The six agent tools (`lookup_customer`, `verify_identity`, `get_claim_status`, `search_faq`, `request_representative`, `complete_call`) | Tools wrap services; authentication and claims now exist, FAQ and escalation do not | Phase 4 (`tools/`) |
| 1.3 | Agent prompts, turn handling, escalation and emergency routing | Depends on tools | Phase 4 (`agents/`) |
| 1.4 | Voice platform webhook + API key enforcement (`VOICE_PLATFORM_API_KEY` is configured but unread) | No conversation to drive yet | Phase 4 (`api/v1/`) |
| 1.5 | FAQ knowledge content | Not needed until the FAQ tool exists | Phase 4 (`knowledge/`) |
| 1.6 | Post-call interaction persistence (Integration #2) | Separate integration from customer/claims | Phase 5 (`integrations/`) |

### From Phase 2 — customer + claim data integration

| # | Item | Why deferred | Lands in |
| - | ---- | ------------ | -------- |
| 2.1 | **Service-account auth for Google Sheets.** Reads currently use an API key, which requires the sheet to be link-shared. | An API key covers every read Phase 2 needs. Writes (Integration #2) cannot use one, and link-sharing is not acceptable for real records — so the work belongs with the phase that first requires it. The client already takes its credentials through one constructor, so this is additive. | Phase 5 |
| 2.2 | **Caching of sheet reads.** Every lookup fetches and scans the whole sheet. | Correct and fast enough for a demo-sized list, and a stateless repository is easier to reason about. Worth revisiting only if call latency shows it: a short TTL cache would cut two round trips per call, at the cost of a window where a just-edited row is stale. | Only if measured |
| 2.3 | **Server-side filtering of claims by customer.** The Sheets API cannot filter; the Query API or a real backend could. | Same reasoning as 2.2 — the fix is to leave Sheets, not to optimise around it. | With the real backend |
| 2.4 | **Multiple claims per caller.** `get_claim_for_customer` returns the most recently updated claim. | Handling "which of your three claims?" is a conversation-design problem, not a data-access one, and belongs with the agent. The repository already parses every claim, so exposing a list later is a small change. | Phase 4, if scoped |

### From Phase 3 — conversation state and authentication

| # | Item | Why deferred | Lands in |
| - | ---- | ------------ | -------- |
| 3.1 | **Sessions do not survive a restart or spread across replicas.** `InMemorySessionStore` is process-local. | A call is handled by one process for its lifetime, and a dropped call loses nothing that matters. `SessionStore` is a protocol, so Redis is an implementation swap — but adding a datastore this assignment does not need would be exactly the distributed infrastructure CLAUDE.md §23 warns against. | Only if deployed multi-replica |
| 3.2 | **Abandoned sessions are never evicted.** `discard` exists but nothing calls it on a timeout. | Bounded in practice by process lifetime and a demo-scale call volume. The right trigger is call completion, which arrives with the post-call phase. | Phase 5 (`complete_call`) |
| 3.3 | **Escalation records are not written.** Session state tracks `escalated` and `escalation_reason`, but no structured escalation record (id, timestamp, status) is created. | CLAUDE.md §13 wants a record with an escalation id and status; that is an integration concern, and it belongs with the tool that requests it. | Phase 4 (`services/escalation.py`) |
| 3.4 | **`conversation_outcome` is set but not yet persisted anywhere.** | It feeds the post-call interaction record, which is Integration #2. | Phase 5 |
| 3.5 | **The verification value is a date of birth.** A single static secret, checked in constant time but not rate-limited beyond the three-attempt budget. | Adequate for a take-home demo and the sheet schema the brief specifies. Real deployments would want a rotating value, or a second factor, and rate limiting per phone number across calls rather than only within one. | Not scoped |

---

## Closed

| # | Item | Closed by |
| - | ---- | --------- |
| 1.x | Retry-with-backoff helper (Phase 1 configured the budget but shipped no executor) | Phase 2 — `core/retry.py`, bounded with jittered exponential backoff |
| 1.x | Readiness dependency registration (Phase 1 shipped an empty registry) | Phase 2 — both repositories register, so `/ready` reports 503 when the sheet is unreachable or misshapen |
| 1.1 | Session state model and the authentication boundary | Phase 3 — frozen `SessionState`, the `AuthenticationService` state machine, and `require_authenticated` as the single claim-access gate |
| 2.5 | Repositories unreachable from a request | Phase 3 — `ServiceContainer` is built at startup and served through `api/dependencies.py`, which returns 503 when the integration is absent |
