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
### From Phase 2 — customer + claim data integration

| # | Item | Why deferred | Lands in |
| - | ---- | ------------ | -------- |
| 2.2 | **Caching of sheet reads.** Every lookup fetches and scans the whole sheet. | Correct and fast enough for a demo-sized list, and a stateless repository is easier to reason about. Worth revisiting only if call latency shows it: a short TTL cache would cut two round trips per call, at the cost of a window where a just-edited row is stale. | Only if measured |
| 2.3 | **Server-side filtering of claims by customer.** The Sheets API cannot filter; the Query API or a real backend could. | Same reasoning as 2.2 — the fix is to leave Sheets, not to optimise around it. | With the real backend |
| 2.4 | **Multiple claims per caller.** `get_claim_for_customer` returns the most recently updated claim. | Handling "which of your three claims?" is a conversation-design problem, not a data-access one, and belongs with the agent. The repository already parses every claim, so exposing a list later is a small change. | Phase 4, if scoped |

### From Phase 3 — conversation state and authentication

| # | Item | Why deferred | Lands in |
| - | ---- | ------------ | -------- |
| 3.1 | **Sessions do not survive a restart or spread across replicas.** `InMemorySessionStore` is process-local. | A call is handled by one process for its lifetime, and a dropped call loses nothing that matters. `SessionStore` is a protocol, so Redis is an implementation swap — but adding a datastore this assignment does not need would be exactly the distributed infrastructure CLAUDE.md §23 warns against. | Only if deployed multi-replica |
| 3.2 | **Sessions from calls that never send an end-of-call event are not evicted.** Normal completion now discards them. | The remaining leak is a call that drops without Vapi reporting it. A TTL sweep would close it; process lifetime bounds it in practice. | Only if long-running |
| 3.5b | **Escalation records are held in memory, not persisted.** Nothing routes them to a queue either — a record is created and logged, and a human would find it in the logs. The record is created and logged; nothing writes it to an external system. | Persisting it belongs with Integration #2, which is where external writes get their client and retry policy. | Phase 7 |
| 3.5 | **The verification value is a date of birth.** A single static secret, checked in constant time but not rate-limited beyond the three-attempt budget. | Adequate for a take-home demo and the sheet schema the brief specifies. Real deployments would want a rotating value, or a second factor, and rate limiting per phone number across calls rather than only within one. | Not scoped |

### From Phase 4 — authenticated claims support

| # | Item | Why deferred | Lands in |
| - | ---- | ------------ | -------- |
| 4.1 | **Multiple claims are still collapsed to the most recent.** `get_claim_status` answers about one claim. | Unchanged from 2.4: "which of your three claims?" is a conversation-design problem. The tool would need a disambiguation turn, which belongs with the agent. | Phase 5, if scoped |
| 4.2 | **Guidance is loaded once at startup.** Editing `claim_guidance.json` needs a restart. | A reload endpoint is more surface than a take-home needs, and a restart is a deploy. Worth adding only if the claims team edits content live. | Not scoped |
| 4.3 | **The voice layer is English-only, with hard-coded connective wording.** Statuses and next steps are configurable; the sentences joining them are not. | Externalising sentence templates buys nothing until there is a second language or a brand-voice review. The configured content already covers everything factual. | Not scoped |
| 4.4 | ~~Submission instructions unreachable~~ — **partly closed in Phase 9**: the caller is now told their claim number when they ask how to send documents. `render_mailing_address` is still uncalled. **`render_submission_instructions` and `render_mailing_address` are still not wired to a tool.** The `document_submission` FAQ entry now covers the same ground, so a caller who asks does get an answer — via `search_faq` rather than as a claim follow-up. | The duplication is small and the FAQ path is well tested. Folding them together needs a decision about whether submission detail belongs to the claim or to the FAQ, which is not worth making under a deadline. | When the duplication bites |

### From Phase 5 — voice platform integration

| # | Item | Why deferred | Lands in |
| - | ---- | ------------ | -------- |
| 5.1 | **No live call has been placed.** The webhook is exercised end to end over HTTP with real Vapi payload shapes, but nobody has dialled a phone number. | Needs a Vapi account, a public tunnel and a phone number — none of which belong in an automated test. `docs/vapi-setup.md` is written to be followed exactly once, then verified against the scenario table. | Manual verification |
| 5.2 | **Transfer is implemented but unverified.** `VOICE_TRANSFER_PHONE_NUMBER` wires a Vapi destination through `voice_platform.py`, and the record becomes TRANSFERRING; but no live Vapi account and no destination exist to test it against. Unset, the realistic escalation workflow runs and is fully tested. | The wire format is written from Vapi's documented destination shape. Confirming it needs an account, a number, and a person to answer — none of which belong in an automated test. CLAUDE.md §13 explicitly accepts the escalation workflow where transfer is unavailable. | Manual verification |
| 5.3 | **The webhook is not idempotent.** Vapi retries a failed delivery; a retried `verify_identity` would spend a second attempt. | The endpoint answers 200 on almost everything, so retries are rare in practice. Proper handling means deduplicating on the tool-call id, which is a small change but needs a store with a TTL. | Before production |
| 5.4 | **FAQ matching is keyword overlap, not semantic.** | Revisited in Phase 6 and kept deliberately: for five documents, keyword matching is exact, instant, dependency-free and deterministic. The specific example that motivated this entry ("When can I reach you?") now matches — the fix was a keyword, not a model. Embeddings remain a bonus-scope idea, worth doing only once there is a measured recall gap. | Only with measured need |
| 5.5 | **No per-call rate limiting on tools.** A model in a loop could call `search_faq` indefinitely. | Vapi bounds call duration, and the authentication budgets bound the paths that matter. A general limiter is real production hardening, not take-home scope. | Before production |

### From Phase 6 — FAQ knowledge base

| # | Item | Why deferred | Lands in |
| - | ---- | ------------ | -------- |
| 6.1 | **Keyword lists are maintained by hand.** A phrasing nobody anticipated scores zero until someone adds the word. | This is the honest cost of the simple mechanism, and it is cheap to pay: the fix is a content edit, not a code change. The failure direction is safe — an unmatched question goes to a representative. Worth revisiting only with logs showing which real questions missed. | Only with measured need |
| 6.2 | **No synonym or stemming support.** "closing time" matches; "shut" does not. | A stemmer is a dependency and a source of surprising matches; a synonym table is another file to keep in step. Neither is justified for five documents. | Only with measured need |
| 6.3 | **`faq.lookup` logs the score but nothing aggregates it.** There is no report of which questions are missing. | Needs a log sink and a query, which is deployment work rather than application work. The data is already in the logs for whoever wants it. | Deployment |
| 6.4 | **The demo disclaimers are enforced by a test, not by the loader.** A new knowledge file without one would load fine. | The test covers the shipped files, which is what ships. Making the loader require it would be one more way for a content edit to break startup, for no gain while the content set is this small. | Not scoped |

### From Phase 7 — escalation and safety

| # | Item | Why deferred | Lands in |
| - | ---- | ------------ | -------- |
| 7.1 | **Emergency detection is English-only and pattern-based.** A caller describing an emergency in another language, or in wording nobody anticipated, is not detected by the backend. | The agent's instructions are the other detector and are not language-bound, so a missed pattern is not a missed emergency. Making the backend detector multilingual, or model-based, trades determinism and testability for recall we cannot currently measure. | Only with measured need |
| 7.2 | **Detection scans only free-text arguments** (`question`, `notes`). An emergency stated while giving a phone number is caught by the agent, not the backend. | Scanning a digits field invites false positives for no realistic gain. The narrow scope is what keeps precision at zero false positives on the claim-talk corpus. | Not scoped |
| 7.3 | **No escalation queue, priority, or acknowledgement.** An emergency record is logged at ERROR and nothing pages anyone. | Routing is a platform and operations concern — a queue, an on-call rotation, an alerting rule. The record is shaped for a router to consume; wiring one is deployment work. | Deployment |
| 7.4 | **Repeated unsupported questions do not proactively escalate.** A caller asking five things we cannot answer is offered a representative five times rather than being handed over. | Needs a per-session counter and a judgement about the threshold, and the offer is already made every time. Worth doing with real call data showing callers get stuck. | Only with measured need |
| 7.5 | **FAQ recall drops on long mixed utterances.** "I'm calling about the fire at my house last month, how does the claims process work?" scores too low to answer, because the coverage metric divides by every word the caller said. | Found while verifying Phase 7; it is an instance of 6.1/6.2 rather than a new problem, and it fails towards a representative. Fixing it properly means scoring per-clause or capping the denominator — a Phase 6 change, not a safety one, and not worth making mid-phase. | With 6.1 |

### From Phase 8 — post-call processing

| # | Item | Why deferred | Lands in |
| - | ---- | ------------ | -------- |
| 8.1 | **The duplicate check is read-then-write, not atomic.** Two processes filing the same call in the same instant could both write. | The in-process guard covers retries within one instance, which is every realistic case at one process per deployment. Closing it properly needs a lock or a keyed store, which is infrastructure this assignment does not need. Worth doing before running multiple replicas — alongside 3.1, which has the same root cause. | Multi-replica |
| 8.2 | **The duplicate check reads the whole sheet on every call.** | Correct and fast at demo scale, once per completed call. It becomes a problem in the thousands of rows, at which point the answer is to leave Sheets rather than optimise around it. | With a real backend |
| 8.3 | **A permanently failed write is lost.** Bounded retries run inside the client; when they are exhausted the record exists only in the logs. | A durable outbox is the correct answer and is real infrastructure — a queue, a retry worker, and its own failure modes. The record *is* logged in full, so nothing is unrecoverable; it is just not automatic. | Before production |
| 8.4 | **Sentiment is outcome-derived, not tone analysis.** A caller who got what they wanted while being furious about it is recorded POSITIVE. | Deliberate, not an oversight: we never see the audio, and inferring mood from a transcript would be a guess presented as data. Real sentiment needs a model call and would make the record non-deterministic. Documented in the README so nobody over-reads the column. | Only with a measured need |
| 8.5 | **The service account key is passed as a JSON environment variable.** | Correct for a container platform's secret store, which is how this would deploy. A secrets manager integration is deployment work, not application work. | Deployment |

### From Phase 9 — end-to-end integration

| # | Item | Why deferred | Lands in |
| - | ---- | ------------ | -------- |
| 9.1 | **The post-call summary names the claim but not its status.** "Claim CLM-88401 was discussed" rather than "…, under review". | The session records `claim_id` but not the status, so this needs a new field carried for the sake of one sentence. Genuinely useful for reporting, and not worth changing working state-management mid-verification. | Small follow-up |
| 9.2 | **A documents-required call is filed as RESOLVED.** The *call* was resolved — the caller got a clear answer — but the *claim* is still outstanding. | `ConversationOutcome` describes the call, which is the right meaning for an interaction log. Anyone wanting claim state should read the claims sheet. Worth revisiting only if the column gets misread in practice. | Only if misread |
| 9.3 | **The structured tool payloads never reach the model.** Vapi tool results are strings, so the agent gets `speech`; `data` is used internally and for tests. | Returning JSON risks the model reading it aloud, which §16 forbids. Everything the caller needs is in the spoken line, and scenario 5 confirms the follow-up path works. | Not scoped |

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
| 1.6 | Post-call interaction persistence (Integration #2) | Phase 8 — a dedicated sheet, service-account auth, `call_id` idempotency, and a record that cannot affect a caller |
| 2.1 | Service-account auth for Google Sheets | Phase 8 — `ServiceAccountAuthorizer` signs a JWT assertion and exchanges it over our own httpx client; reads keep the API key |
| 3.4 | `conversation_outcome` persisted | Phase 8 — it is the `resolution` column |
| 5.6 | Sentiment captured | Phase 8 — derived from outcome, using the controlled vocabulary |
| 1.5 | FAQ knowledge content | Phase 5 as `faq.json`; restructured in Phase 6 into one Markdown file per topic with demo disclaimers and a confidence indicator |
| 3.3 | Structured escalation records | Phase 5 — `EscalationService` creates id, call, customer, reason, timestamp and status; Phase 7 added REQUESTED/TRANSFERRING status, bounded notes, and asserted the record carries no claim data |
