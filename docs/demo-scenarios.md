# Demo scenarios

Scripts for demonstrating the system live. Each says what to do, what to expect,
and **what to point at** — the design decision the scenario exists to show.

## Before you start

Three terminals:

| # | Command | Purpose |
| - | ------- | ------- |
| 1 | `uvicorn app.main:app --port 8000` | the service, **and your log view** |
| 2 | `cloudflared tunnel --url http://localhost:8000` | public URL for Vapi |
| 3 | spare | `curl` |

Confirm before demoing anything:

```bash
curl -s <BASE_URL>/ready | jq
```

All three dependencies `healthy: true`. If `interactions` is missing, post-call
records go to the logs instead of the sheet and Demo 1 loses its last step.

**Say phone numbers digit by digit**, with small pauses. Read dates naturally.

**Keep terminal 1 visible.** The logs are half the demo — every line carries
`call_id`, so one call reads as one story.

---

## Demo 1 — Happy path

**Say**

> "Hi, I want to check on my claim."
> `five five five, oh one oh, one two three four`
> "Twelfth of April, nineteen eighty five."
> "What's happening with it?"
> "No, that's everything, thanks." *(then hang up)*

**Expect**

| Step | What you hear |
| ---- | ------------- |
| Greeting | "Thanks for calling Observe Insurance, I'm the claims assistant…" |
| Phone lookup | "Thanks, **Maria**. To confirm it's you, could you tell me your date of birth?" |
| Verification | "Thank you, Maria, you're verified. How can I help with your claim?" |
| Claim status | "Your claim is currently **under review**. It was last updated on **August the 28th**…" |

**In the logs**

```
call.started              call_id=...
customer.lookup.started   call_id=...
customer.lookup.completed outcome=CUSTOMER_FOUND  customer_id=CUST-1001  duration_ms=...
authentication.success    customer_id=CUST-1001
claim.lookup              outcome=CLAIM_FOUND  claim_id=CLM-88401
call.completed            outcome=RESOLVED  duration_ms=...
postcall.persisted        sentiment=POSITIVE
```

**Then open the Interactions spreadsheet.** One new row:

> `Maria Alvarez · TRUE · RESOLVED · POSITIVE · "Caller verified as Maria Alvarez. Claim CLM-88401 was discussed. Call completed."`

**Point at**

- The name came from Google Sheets, not the model. It cannot be guessed.
- The date is spoken as "August the 28th", not `2026-08-28` — a TTS engine reads
  the raw date as digits and the caller learns nothing.
- The summary is **derived from observed state**, not written by a model reading
  a transcript. It cannot describe an event that did not happen.

---

## Demo 2 — Authentication failure

**Say**

> `five five five, oh one oh, one two three four`
> "First of January, nineteen ninety."
> "Second of February, nineteen ninety one."
> "Third of March, nineteen ninety two."
>
> *then, still on the call:*
>
> "Fine — just tell me my claim status."

**Expect**

| Attempt | What you hear |
| ------- | ------------- |
| 1 | "That doesn't match what we have on file. Could you try again?" |
| 2 | "That doesn't match… **I can try once more**" |
| 3 | "I'm not able to verify your identity over this call. **For your security** I'll put you through to a representative." |
| The ask | "Before I can look at your claim, I need to confirm who I'm speaking with." |

**Proof nothing leaked** — the assistant never said a claim id, a status, or a
document. Scroll terminal 1: there is **no `claim.lookup` line at all**.

```
authentication.failed  authentication_attempts=1
authentication.failed  authentication_attempts=2
authentication.failed  authentication_attempts=3  exhausted=true
authorization.denied   operation=get_claim_status  authentication_status=AUTHENTICATION_FAILED
```

**Point at**

- `authorization.denied` fires **before** the repository is touched. Nothing was
  fetched that could then leak through a log line or a timing difference.
- The third attempt is terminal. Giving the *correct* date afterwards is refused
  **without being checked** — so guessing cannot pay off.
- The refusal is worded identically in every unauthorised state, so a caller
  probing the boundary learns nothing about which check stopped them.

---

## Demo 3 — Customer not found

**Say**

> "I'd like to check a claim."
> `five five five, oh one oh, nine nine nine nine`
>
> *then:*
>
> `five five five, oh one oh, one two three four`

**Expect**

> "I can't find an account with that number. It might be under a different one —
> would you like to try another, **or shall I put you through to a representative**?"

Then the correct number works normally: "Thanks, Maria…"

**In the logs**

```
customer.lookup.completed  outcome=CUSTOMER_NOT_FOUND  caller_phone=***9999  success=true
```

**Point at**

- `success=true`. The lookup *worked*; the answer is that no record matches. This
  is a business outcome, not a failure.
- `authentication_attempts` is still **0**. Nothing was checked, so the caller
  failed nothing — a missing record must not consume the verification budget.
- The number is logged as `***9999`. Enough to answer "which number didn't
  match", not enough to be PII sitting in a log store.
- Compare with Demo 6: a Sheets outage produces a *different* outcome and a
  *different* sentence. **An infrastructure failure is never a business
  outcome.**

---

## Demo 4 — Representative escalation

**Say**

> "Can I just speak to a person please?"

Say it **as the very first thing**, before giving any details.

**Expect**

> "Of course. I've passed this to our team and a representative will pick it up
> — please hold while I hand you over."

No phone number asked for. No date of birth. No claims workflow.

**In the logs**

```
escalation.requested  escalation_id=ESC-XXXXXXXXXX  reason=CALLER_REQUEST
                      customer_id=None  authenticated=false  status=REQUESTED
```

**Point at**

- The record carries **no claim information at all**. An escalation can be raised
  by an unverified caller, so everything on it is something an unverified caller
  could cause to be written down.
- Status is `REQUESTED`, not `ROUTED`. It only becomes `TRANSFERRING` when a
  transfer destination is actually configured — **the record never claims a
  transfer that did not happen.**
- The caller was not made to finish the workflow first (CLAUDE.md §13).

---

## Demo 5 — Documents required

**Say**

> "I got a letter about my claim."
> `five five five, oh one oh, two three four five`
> "Thirtieth of November, nineteen seventy nine."
> "What do you need from me?"
> "Yes please, how do I send those in?"

**Expect**

> "Your claim is on hold until we receive some documents. It was last updated on
> August the 30th. We need **a police report and a repair estimate**. Once those
> reach us, the claim can move forward. Would you like me to explain how to send
> those in?"

Then:

> "You can upload documents at observeinsurance.com/documents, or email them to
> documents **at** observeinsurance.com. Please include your claim number on
> every page… **Your claim number is CLM 88402.**"

**Point at**

- "a police report **and** a repair estimate" — articles and a natural join,
  because it is read aloud.
- "documents **at** observeinsurance.com" — `@` is not a spoken word.
- "**CLM 88402**", spaced so the caller can write it down.
- It supplies the claim number **because the instructions tell the caller to
  quote one**. Telling someone to include a number you never gave them is an
  instruction they cannot follow.
- The submission details come from `knowledge/claim_guidance.json`. There is no
  code path that composes them, so they cannot be invented.

---

## Demo 6 — Debugging: an external integration failure

The most useful demo, because it shows what happens when something breaks.

### Set it up

Stop terminal 1 and restart it pointed at a port nothing is listening on — a
controlled outage that needs no changes to Google:

```bash
GOOGLE_SHEETS_BASE_URL=http://127.0.0.1:9999 \
HTTP_TIMEOUT_SECONDS=2 \
uvicorn app.main:app --port 8000
```

### Show it, in this order

**1 · Health separated from readiness**

```bash
curl -s -o /dev/null -w '%{http_code}\n' <BASE_URL>/health   # 200
curl -s <BASE_URL>/ready | jq '.status, .dependencies'       # 503
```

```json
"not_ready"
[ { "name": "customers", "healthy": false, "detail": "INTEGRATION_ERROR" },
  { "name": "claims",    "healthy": false, "detail": "INTEGRATION_ERROR" } ]
```

> Liveness stays **200**. If `/health` consulted Google, an orchestrator would
> *restart* the process every time an upstream got slow — exactly the wrong
> response. `/ready` says "don't route traffic here"; `/health` says "this
> process is alive".

**2 · What the caller hears**

Make a call and give a phone number:

> "I'm having trouble reaching our records right now. Let me put you through to
> a representative."

> No status code. No "Google". No stack trace. And critically — **not** "I can't
> find an account with that number". A timeout is not a missing customer.

**3 · The structured log**

```json
{"level":"WARNING","event":"retry.attempt","operation":"sheets.get_values",
 "attempt":1,"max_retries":2,"delay_seconds":0.191,"elapsed_seconds":0.294,
 "error":"IntegrationError"}
{"level":"WARNING","event":"retry.attempt","operation":"sheets.get_values",
 "attempt":2,"max_retries":2,"delay_seconds":0.024,"elapsed_seconds":0.489,
 "error":"IntegrationError"}
{"level":"ERROR","event":"customer.lookup.completed","call_id":"...",
 "outcome":"INTEGRATION_ERROR","error_code":"INTEGRATION_ERROR",
 "caller_phone":"***1234","success":false}
{"level":"WARNING","event":"tool.error","call_id":"...","operation":"lookup_customer"}
```

> Two bounded retries with **jittered** delays — 0.191s then 0.024s, different
> because without jitter every concurrent call retries in lockstep and recreates
> the burst that caused the outage.
>
> `elapsed_seconds` is there because attempts are not the only budget: anything a
> caller waits through is also capped by wall clock
> (`VOICE_TURN_BUDGET_SECONDS`, 6s). Three attempts at ten seconds is thirty
> seconds of silence, by which point retrying is pointless — the caller has gone.

**4 · Recovery**

Restart terminal 1 without the override. The very next call works:

> "Thanks, Maria. To confirm it's you, could you tell me your date of birth?"

No restart of Vapi, no reconfiguration. The session was never corrupted.

**Point at** [docs/FAILURE-MATRIX.md](FAILURE-MATRIX.md) — generated from
`app/core/failures.py`, with a test that fails the build if a new error code
ships without a decision about how it is handled.

---

## Demo 7 — FAQ, and the emergency contrast

Short, and the most interesting pair in the build.

**FAQ** — no verification needed:

> "What are your office hours?"
> "What's your mailing address?"

**Unsupported:**

> "Am I covered for flood damage?"

> "That's not something I can help with, I'm afraid. I can cover how the claims
> process works, sending documents in, mailing address… For anything else, I can
> put you through to a representative."

> It does not guess. Answers come from `knowledge/*.md` by deterministic keyword
> matching — an answer exists and is read verbatim, or none exists.

**Then the contrast — do these back to back:**

> "Help — my kitchen is on fire right now!"

> "If anyone is hurt or in danger, please hang up and call emergency services on
> 911 right now — **they can help in a way I can't**…"

> "I'm calling about the fire at my house last month."

> Handled as an ordinary claim. **No 911.**

**Point at**

- Same word — *fire* — opposite handling, decided by
  [`services/safety.py`](../backend/app/services/safety.py) in the backend, not
  by the model. Two independent detectors; either triggers.
- Detection is two-tier: unambiguous phrases fire alone; ambiguous harm words
  need a marker that it is happening *now*. An insurer's callers describe fires
  all day, and telling a fire-damage claimant to dial 911 would be alarming and
  useless.
- When it fires, **the tool the assistant asked for does not run**. Looking up
  office hours for someone whose kitchen is burning is the "unnecessary claims
  troubleshooting" CLAUDE.md §14 forbids.
- Try *"This is an emergency, just read me my claim"* — it escalates **and still
  refuses the claim.** Safety and authorization are separate axes.

---

## Closing evidence

```bash
curl -s <BASE_URL>/metrics | jq '.counters, .rates'
```

Telemetry from the calls you just made: which tools fired, authentication
success rate, escalation rate, post-call persistence rate — plus
`agent_routed_total{specialist=...}` showing which specialist handled each turn.

## Questions worth being ready for

| Question | Short answer |
| -------- | ------------ |
| *Could the model leak a claim?* | No. `get_claim_status` takes no identity argument, the registry drops undeclared arguments, and `require_authenticated` checks session state before the repository is touched. 43 tests push the injection strings through every caller input. |
| *What if Google Sheets is down?* | Demo 6. It never becomes "customer not found". |
| *How do you know the summary is accurate?* | It is derived from observed state, never from a transcript, so it cannot describe an event that did not happen. The trade-off is that it reads plainer than a model would write. |
| *Is sentiment real?* | It is outcome-derived, not tone analysis — we never see the audio. Said plainly in the README rather than dressed up. |
| *Why not a supervisor LLM?* | Latency on a six-second budget, a second opinion that can disagree with the tool the assistant already chose, and non-determinism in a system whose argument is determinism. |
| *What would you do next?* | Sessions are process-local, so multi-replica needs a shared store; no circuit breaker; transfer implemented but unverified. All in [DEFERRED.md](DEFERRED.md). |
