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
| 1.6 | Post-call interaction persistence (Integration #2) — caller name, summary, sentiment, timestamp | Separate integration from customer/claims. The `end-of-call-report` webhook that carries the summary is now handled, so the hook exists | Phase 7 (`integrations/`) |

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
| 3.2 | **Sessions from calls that never send an end-of-call event are not evicted.** Normal completion now discards them. | The remaining leak is a call that drops without Vapi reporting it. A TTL sweep would close it; process lifetime bounds it in practice. | Only if long-running |
| 3.5b | **Escalation records are held in memory, not persisted.** The record is created and logged; nothing writes it to an external system. | Persisting it belongs with Integration #2, which is where external writes get their client and retry policy. | Phase 7 |
| 3.4 | **`conversation_outcome` is set but not yet persisted anywhere.** | It feeds the post-call interaction record, which is Integration #2. | Phase 7 |
| 3.5 | **The verification value is a date of birth.** A single static secret, checked in constant time but not rate-limited beyond the three-attempt budget. | Adequate for a take-home demo and the sheet schema the brief specifies. Real deployments would want a rotating value, or a second factor, and rate limiting per phone number across calls rather than only within one. | Not scoped |

### From Phase 4 — authenticated claims support

| # | Item | Why deferred | Lands in |
| - | ---- | ------------ | -------- |
| 4.1 | **Multiple claims are still collapsed to the most recent.** `get_claim_status` answers about one claim. | Unchanged from 2.4: "which of your three claims?" is a conversation-design problem. The tool would need a disambiguation turn, which belongs with the agent. | Phase 5, if scoped |
| 4.2 | **Guidance is loaded once at startup.** Editing `claim_guidance.json` needs a restart. | A reload endpoint is more surface than a take-home needs, and a restart is a deploy. Worth adding only if the claims team edits content live. | Not scoped |
| 4.3 | **The voice layer is English-only, with hard-coded connective wording.** Statuses and next steps are configurable; the sentences joining them are not. | Externalising sentence templates buys nothing until there is a second language or a brand-voice review. The configured content already covers everything factual. | Not scoped |
| 4.4 | **`render_submission_instructions` and `render_mailing_address` are still not wired to a tool.** The `document_submission` FAQ entry now covers the same ground, so a caller who asks does get an answer — via `search_faq` rather than as a claim follow-up. | The duplication is small and the FAQ path is well tested. Folding them together needs a decision about whether submission detail belongs to the claim or to the FAQ, which is not worth making under a deadline. | When the duplication bites |

### From Phase 5 — voice platform integration

| # | Item | Why deferred | Lands in |
| - | ---- | ------------ | -------- |
| 5.1 | **No live call has been placed.** The webhook is exercised end to end over HTTP with real Vapi payload shapes, but nobody has dialled a phone number. | Needs a Vapi account, a public tunnel and a phone number — none of which belong in an automated test. `docs/vapi-setup.md` is written to be followed exactly once, then verified against the scenario table. | Manual verification |
| 5.2 | **`request_representative` does not transfer the call.** It creates an escalation record and tells the caller they are being put through; the call then continues with the assistant. | Real transfer needs a Vapi `transferCall` destination and somewhere to transfer *to*. CLAUDE.md §13 explicitly accepts a realistic escalation workflow where transfer is unavailable. | When a destination exists |
| 5.3 | **The webhook is not idempotent.** Vapi retries a failed delivery; a retried `verify_identity` would spend a second attempt. | The endpoint answers 200 on almost everything, so retries are rare in practice. Proper handling means deduplicating on the tool-call id, which is a small change but needs a store with a TTL. | Before production |
| 5.4 | **FAQ matching is keyword overlap, not semantic.** | Revisited in Phase 6 and kept deliberately: for five documents, keyword matching is exact, instant, dependency-free and deterministic. The specific example that motivated this entry ("When can I reach you?") now matches — the fix was a keyword, not a model. Embeddings remain a bonus-scope idea, worth doing only once there is a measured recall gap. | Only with measured need |
| 5.5 | **No per-call rate limiting on tools.** A model in a loop could call `search_faq` indefinitely. | Vapi bounds call duration, and the authentication budgets bound the paths that matter. A general limiter is real production hardening, not take-home scope. | Before production |
| 5.6 | **Sentiment is not captured.** `Sentiment` exists in the vocabulary but nothing produces one. | It belongs to the post-call record, which is Integration #2. | Phase 7 |

### From Phase 6 — FAQ knowledge base

| # | Item | Why deferred | Lands in |
| - | ---- | ------------ | -------- |
| 6.1 | **Keyword lists are maintained by hand.** A phrasing nobody anticipated scores zero until someone adds the word. | This is the honest cost of the simple mechanism, and it is cheap to pay: the fix is a content edit, not a code change. The failure direction is safe — an unmatched question goes to a representative. Worth revisiting only with logs showing which real questions missed. | Only with measured need |
| 6.2 | **No synonym or stemming support.** "closing time" matches; "shut" does not. | A stemmer is a dependency and a source of surprising matches; a synonym table is another file to keep in step. Neither is justified for five documents. | Only with measured need |
| 6.3 | **`faq.lookup` logs the score but nothing aggregates it.** There is no report of which questions are missing. | Needs a log sink and a query, which is deployment work rather than application work. The data is already in the logs for whoever wants it. | Deployment |
| 6.4 | **The demo disclaimers are enforced by a test, not by the loader.** A new knowledge file without one would load fine. | The test covers the shipped files, which is what ships. Making the loader require it would be one more way for a content edit to break startup, for no gain while the content set is this small. | Not scoped |

---

## Closed

| # | Item | Closed by |
| - | ---- | --------- |
| 1.x | Retry-with-backoff helper (Phase 1 configured the budget but shipped no executor) | Phase 2 — `core/retry.py`, bounded with jittered exponential backoff |
| 1.x | Readiness dependency registration (Phase 1 shipped an empty registry) | Phase 2 — both repositories register, so `/ready` reports 503 when the sheet is unreachable or misshapen |
| 1.1 | Session state model and the authentication boundary | Phase 3 — frozen `SessionState`, the `AuthenticationService` state machine, and `require_authenticated` as the single claim-access gate |
| 2.5 | Repositories unreachable from a request | Phase 3 — `ServiceContainer` is built at startup and served through `api/dependencies.py`, which returns 503 when the integration is absent |
| 1.2 (part) | `get_claim_status` tool | Phase 4 — with a structured response, a configured next step, and a voice rendering |
| 1.2 | The remaining four tools | Phase 5 — `lookup_customer`, `verify_identity`, `search_faq`, `request_representative`, behind a registry that is the whole attack surface |
| 1.3 | Agent prompt, turn structure, emergency routing | Phase 5 — `app/agents/prompts/claims_agent.md`, carrying behaviour but no business rules |
| 1.4 | Voice platform webhook and secret enforcement | Phase 5 — `POST /api/v1/voice/webhook`; production refuses to start without a secret |
| 1.5 | FAQ knowledge content | Phase 5 as `faq.json`; restructured in Phase 6 into one Markdown file per topic with demo disclaimers and a confidence indicator |
| 3.3 | Structured escalation records | Phase 5 — `EscalationService` creates id, call, customer, reason, timestamp and status |
