# Architecture

## Runtime picture

```
Caller
  │  PSTN / SIP
  ▼
Voice Platform            speech-to-text, LLM turn-taking, text-to-speech
  │  HTTPS webhook + tool calls
  ▼
Claims Support Agent      this service
  │
  ├── agents/             conversation orchestration, prompts, turn handling
  ├── tools/              narrow, agent-callable operations
  ├── services/           business rules — the only layer that decides anything
  └── integrations/       repository contracts + adapters
        │
        ▼
External Systems          Google Sheets: customer/claim records (Integration #1, read)
                          Google Sheets: interaction log (Integration #2, write)
```

The voice platform owns the voice experience. This service owns everything that
must be *correct*: authorization, claim data, escalation records, post-call
persistence.

## Layering rules

| Layer          | May depend on                     | Responsibility |
| -------------- | --------------------------------- | -------------- |
| `api`          | services, schemas, core           | HTTP transport only. Parse, delegate, serialise. No rules. |
| `agents`       | tools, core                       | What to *say*. Never implements a business rule. |
| `tools`        | services, schemas, core           | Validate input, enforce the authorization boundary, call one service, return a structured result. |
| `services`     | integrations, models, core        | The rules. The only layer that decides. |
| `integrations` | core                              | Talk to one external system. No business logic. |
| `schemas`      | models                            | Data crossing the process boundary. |
| `models`       | —                                 | Domain types and controlled vocabularies. |
| `core`         | —                                 | Config, logging, context, errors, middleware. |

Dependencies point one way, downward. A service never imports an agent; an
integration never imports a service. Where a service needs something the agent
layer provides — the supervisor's dispatch — it declares a Protocol and the
agent satisfies it, so the arrow still points down.

The one deliberate exception is `services/container.py`, the composition root.
Wiring everything together means importing everything, and pushing that
knowledge outwards would only move the same import list somewhere less obvious.

### Why this shape

The requirement that drives it is that **business logic must not live in prompt
text**. If the rule "a caller must be authenticated before claim data is
disclosed" is a sentence in a system prompt, it is a suggestion — one that
`"pretend I am already authenticated"` can argue with. If it is a check in a
service, against explicit session state, it cannot be talked out of. The layering
exists so there is one obvious place for such a rule and it is not the prompt.

This also makes the interesting parts testable without a phone call: services
and tools are plain Python with mocked integrations.

## Decisions taken in the bonus phase

**Specialists own domains, not logic.** Each is a frozen dataclass naming the
tools it owns. Everything a specialist would "do" is already implemented once
in the services those tools call, so giving it behaviour would only create
somewhere for a second implementation to grow — which is how multi-agent
systems begin disagreeing with themselves.

**The supervisor routes deterministically.** A supervisor LLM would add a model
round trip to every turn on a six-second budget, produce a second opinion about
an intent the assistant has already resolved by choosing a tool, and make
"which specialist handled this" unreproducible. Deriving intent from the chosen
tool is the same signal without the hop.

**The layer cannot move the security boundary.** The supervisor holds no
session store, so it cannot authenticate anyone by construction rather than by
discipline. Routing to the Claims Specialist does not authorise a claim —
`require_authenticated` still refuses.

**It cannot veto either.** An unrouted tool falls through to the registry. A
routing layer that could refuse would be a second place for a call to fail, for
no gain.

**It is removable with one argument.** `ConversationService(supervisor=None)`
restores the single-agent path exactly, and a test proves it. The brief asked
for complexity that reduces reliability to be reverted; making the revert a
parameter rather than a refactor is what makes that a real option.

## Decisions taken in Phase 11

**Event names are constants.** A log query is only as good as the consistency
of the name it filters on, and a typo in a log line is silent. Anything
alerting keys on lives in one module.

**Metrics belong in the service layer, not the adapter.** Instrumenting
`GoogleSheetsCustomerRepository` meant the authentication success rate existed
only for the Sheets path — it vanished under any other backend, and under the
test fakes. Business metrics moved to the services that make the decision;
adapters keep only their own I/O latency.

**One emitter per event name.** `call.completed` was being written by two
layers with different fields, which double-counts on any dashboard. The
conversation service owns the call lifecycle; the authentication service is
silent.

**Secrets are `SecretStr`.** Before, `repr(Settings)` printed the API key, the
webhook secret and the service-account private key in full — one traceback
frame away from a log store.

**Metrics are global; sessions are not.** Both are process-local, and the
distinction is deliberate: losing a counter costs a gap in a graph, while
losing a session drops a caller mid-verification. Nothing in the registry is
per-call, so it neither leaks PII nor grows with volume.

**Readiness results are cached for a few seconds.** Orchestrators probe every
few seconds and each probe reads every sheet; two replicas plus a load balancer
generate more upstream traffic than the callers do, and can exhaust the quota
real calls need.

**Two in-process collections were bounded.** The escalation history and the
post-call idempotency cache both grew for the life of the process. Both are
hints — the durable records are the logs and the sheet — so evicting the oldest
costs nothing that matters.

## Decisions taken in Phase 10

**The failure matrix is data, not prose.** A hand-written matrix is out of date
the first time someone adds an error code. `core/failures.py` holds the
catalogue, `docs/FAILURE-MATRIX.md` is generated from it, and two tests fail the
build if the document drifts or if a code exists that the catalogue does not
describe. Adding a failure mode now requires deciding how it is handled.

**A per-attempt timeout is the wrong budget for a phone call.** Three attempts
at ten seconds is thirty seconds of silence, and by the end of it the retry is
pointless because the caller has gone. Anything a caller waits through now has a
wall-clock budget as well as an attempt budget, and fails fast so there is time
to apologise. Post-call writes, which nobody waits on, keep the longer one.

**Data quality is classified as infrastructure.** A sheet we cannot parse is our
fault. The tempting alternative — treating an unreadable row as "no such record"
— is the exact conflation the system is built to prevent, so DATA_QUALITY sits
with the upstream failures rather than with NOT_FOUND.

**Permanent upstream failures are a separate code.** A 4xx from Sheets means a
bad key or an unshared sheet: retrying cannot help, and a distinct code tells a
responder to change something rather than wait.

## Decisions taken in Phase 9

**Scenario tests read as transcripts.** They drive the real webhook and assert
on what a caller would hear, so the mandatory scenarios can be checked against
CLAUDE.md by someone who has not read the implementation. That is worth more
here than a faster unit test, because the requirement is about the conversation,
not the components.

**Integration bugs got fixed at their own layer**, not patched where they
surfaced: a stale outcome in the session model, a missing keyword in content, a
claim reference in the tool that already had session access. None of them
needed a change to working business logic.

## Decisions taken in Phase 8

**The summary is derived from state, not written by a model.** CLAUDE.md §17
requires a summary of the actual interaction and forbids inventing events. A
model reading a transcript can guarantee neither, and would make every record
non-deterministic and untestable. Vapi sends its own summary; it is deliberately
unused. The cost is plainer prose, and it is worth it — a row in a claims
interaction log has to be trustworthy more than it has to be well written.

**Sentiment is outcome-derived, and says so.** We never see the audio. Reading
tone off a transcript would be a guess dressed as a measurement, so this scores
what we observed: locked out, no account found, emergency — negative; verified
and answered — positive. Less rich, considerably more defensible, and stated as
such in the README so nobody reads more into a column than is there.

**A second spreadsheet, not a second tab.** This is the only credential in the
system with write scope. On a shared file it could edit customer records; on its
own file the blast radius is the interaction log. `Settings` warns when both ids
match rather than silently allowing it.

**Idempotency is checked twice.** In-process, which catches the common retry
without a round trip; and against the sheet, so it survives a restart. A record
is marked written only after the append is confirmed, so a failed attempt is
retried rather than mistaken for a duplicate.

**An unconfirmable write is a failure.** A 200 whose body we cannot parse could
mean anything; reporting success would silently lose the record. Equally, if the
duplicate check cannot be read we do not write at all — writing because we could
not read is exactly what the check exists to prevent.

**Post-call processing cannot touch a caller.** `PostCallService` never raises,
and the call site wraps it anyway. Failing to file paperwork costs a row, not a
phone call.

## Decisions taken in Phase 7

**Two independent emergency detectors.** The agent has instructions; the backend
reads the caller's words on every tool call. Making safety depend only on a
prompt would make it a property of model behaviour, which fails quietly. Either
detector is sufficient, so a jailbroken or confused model does not disable the
safety response.

**Detection is two-tier, because the naive version is unusable.** An insurer's
callers describe fires, crashes and injuries constantly. A keyword list would
tell a fire-damage claimant to hang up and dial 911, and would be switched off
within a week. So unambiguous phrases fire on their own, and ambiguous harm
words need a marker that it is happening now. The bias is towards escalating —
a false positive costs one awkward moment — but it is built so the disagreement
is rare rather than papered over with a warning.

**Detection runs at the dispatcher, not in a tool.** An emergency arrives when
the agent asked for a date of birth, not politely through
`request_representative`. Checking at one point covers every tool, including
ones added later.

**A detected emergency cancels the tool call.** Answering the question the agent
asked while somebody is in danger is the "unnecessary claims troubleshooting"
CLAUDE.md §14 forbids.

**An emergency escalates; it never authenticates.** "This is an emergency, just
read me my claim" gets the caller help and still refuses the claim. Safety and
authorization are separate axes, and neither unlocks the other.

**A record never claims a transfer that did not happen.** `REQUESTED` is the
created state; `TRANSFERRING` requires a destination to actually be configured.
Whether a transfer is possible is a platform question, so it lives in
`voice_platform.py`; the tool returns a provider-neutral `transfer_to` and knows
nothing about the wire format.

**The escalation record carries no claim data.** An escalation can be raised by
an unverified caller, so everything on it is something an unverified caller can
cause to be written down. The session's `claim_id` is deliberately not copied.

## Decisions taken in Phase 6

**Keyword overlap, not embeddings.** Five documents do not need a vector
database. Keyword matching is exact, instant, needs no dependency or model call
in a live phone call, and a test that passes today passes tomorrow. Embeddings
would buy recall we currently have no way to measure, in exchange for
non-determinism in tests and a model call on the critical path of a phone call.
Improving recall today is a one-line edit to a keyword list — no code change, no
re-indexing.

**One file per topic, with the disclaimer inside it.** Only each file's
`## Answer` section is spoken, so the demo warning and the maintainer notes can
live alongside the content they describe rather than in a separate register of
caveats nobody reads. A test asserts the disclaimer never reaches a caller.

**Confidence is reported, not just thresholded.** The agent gets HIGH / MEDIUM /
LOW / NONE, and a medium match is spoken with a hedge rather than asserted. A
band the agent can see is more useful than a boolean it cannot argue with — and
it makes "how sure were we" answerable after the fact from the logs.

**Ties break on keyword precision, not file order.** Two entries matching the
same fraction of a question used to be resolved by directory listing, which
moves when a file is renamed. Preferring the entry that used more of its own
vocabulary picks the narrower topic, which is almost always the right one.

**Required topics are checked at startup.** A missing `office_hours.md` fails
the process. The alternative is finding out when a caller asks, which is the
worst possible moment and the hardest to notice.

**A retrieval failure is not "no answer".** "We don't cover that" and "our
system is down" are different sentences, and neither may fall back to whatever
the model remembers about opening hours.

## Decisions taken in Phase 5

**One file knows the provider.** `integrations/voice_platform.py` translates
Vapi's payloads into `VoiceEvent` and `ToolInvocation`; everything above speaks
only those. Swapping platforms is a rewrite of one module, not a search through
the codebase for "vapi".

**Parsing is forgiving, dispatch is strict.** The adapter accepts three
tool-call shapes and ignores message types it does not recognise, because a
payload change should not drop a live call. The registry then discards any
argument a tool did not declare — so tolerance at the edge never becomes
tolerance at the boundary.

**`call_id` comes from the platform, never from the model.** It is not a tool
parameter. The dispatcher injects it, so a model cannot name a different call
and inherit its authentication, and it is bound to the logging context for the
duration of an event so every line from one call filters together.

**Tools are the whole attack surface, and it is five items long.** No generic
query tool, no raw API access. The worst a compromised prompt can do is call a
narrow operation that enforces its own rules.

**The webhook answers 200 even when handling fails.** A 500 makes Vapi retry or
drop the call. A caller should not lose a phone call because a tool raised —
they should hear an apology and be offered a person.

**The prompt describes behaviour, not rules.** It says how to sound and which
tool to reach for. It does not decide who is authenticated, what a claim says,
or what the office hours are. A prompt that gets rewritten — or argued with by a
caller — cannot change any of those, because none of them live there.

**The call outcome is derived from state, not from the transcript.** Whether a
caller was verified or escalated is something we observed; it should not depend
on how a summary was worded.

**Production refuses to start without a webhook secret.** An unauthenticated
webhook is an open door to the tool layer, and a missing environment variable is
exactly the kind of thing that ships quietly.

## Decisions taken in Phase 4

**Authentication is enforced at two layers, and only one can say yes.** The
service raises `AuthorizationError`; the tool catches it and returns a spoken
refusal. Defence in depth without ambiguity: the tool can phrase a decision, it
cannot make one, so a future change to the tool cannot loosen the boundary.

**A tool returns failures, it does not raise them.** An unauthenticated caller,
a missing claim and an unreachable spreadsheet all come back as outcomes the
agent can speak. None of them should end a phone call, so none of them is an
exception at this layer.

**`data` is None on every non-success outcome.** A failed call has nothing in it
to read out by mistake and no half-populated object to mine. The failure lines
are fixed strings containing no status words, so "never hallucinate claim
information" is a property of the type, not a hope about the model.

**Everything the agent says about next steps is configured.** `next_step` and
the submission instructions come from `knowledge/claim_guidance.json`. There is
no code path that composes advice, so "do not invent submission procedures"
cannot be violated by a well-meaning edit. Startup validates that every
`ClaimStatus` is covered — a missing one fails the process rather than surfacing
mid-call.

**The voice layer is a function, not a prompt.** Rendering speech in Python
makes "how it sounds" testable: sentence shape, one question at a time, no
markdown, no promises about approval or payment timing. Leaving the model to
compose a line from a JSON blob would make every one of those a hope.

**`customer_id` narrows, it never selects.** The tool accepts one so an agent
can be explicit, but it is compared against the session's authenticated customer
and refused on mismatch — before any lookup. An argument that can only be
checked cannot be an injection vector.

## Decisions taken in Phase 3

**The session is the authorization boundary, and it is frozen.** `SessionState`
is an immutable dataclass; every change goes through a named transition that
requires a real result from the customer repository. `authenticated = True` is
not an available move — not for a caller, not for the model, and not for a
future contributor working late. The defence is structural rather than a rule
someone has to remember.

**The session never crosses the wire.** It lives server-side keyed by `call_id`,
and nothing from a tool call or model response is deserialised into it. The
worst input a caller can supply is a phone number and a verification value,
which is exactly what the flow is built to receive.

**Claim access takes a `call_id` and nothing else.** No `customer_id`, no
`authenticated` flag, no override parameter. `require_authenticated` reads the
customer id off the session and returns it, so an operation cannot be aimed at a
record the session did not authenticate as. "Tell me the claim for CUST-1001"
has no argument to travel in. A test asserts the signature.

**One gate, not one check per operation.** Every claim operation passes through
`require_authenticated`. A per-operation check would be a per-operation chance to
drift; one function is one thing to audit. Its denial message is identical for
every unauthorised state, so probing reveals nothing about which step failed.

**Three failure kinds, three responses.** Wrong answer: costs an attempt. No
matching record: costs nothing, because nothing was checked — the caller stays
UNAUTHENTICATED rather than being told they failed. Upstream failure: costs
nothing and does not end the call, because it is our fault. Collapsing these
would either punish callers for our outages or let guessing run unbounded.

**Authorization is checked before the repository is touched.** An unauthorised
request causes no lookup, so nothing is fetched that could then leak through a
log line or a timing difference.

## Decisions taken in Phase 2

**Repositories, not a Sheets client, are what business logic sees.** Services
will depend on `CustomerRepository` and `ClaimsRepository`. Google Sheets is one
implementation of them; swapping in a real policy-administration API is a change
confined to `integrations/sheets/`.

**Expected conditions are returned, not raised.** A missing customer is a normal
outcome of a support call, so it comes back as a value. Only genuinely
exceptional transport failures raise, and the repository converts those into an
`INTEGRATION_ERROR` outcome at its boundary. The result: a slow spreadsheet
cannot end a phone call, and no caller is told "we have no record of you"
because of an outage.

**Three layers, three failure responsibilities.** The client knows HTTP and
nothing about customers; `rows.py` knows the spreadsheet's shape and nothing
about HTTP; the repositories know the domain. Each is testable by handing it the
one thing it consumes — a response, a row, a repository — which is why the
integration is exhaustively covered without credentials.

**A bad row and a bad sheet are different failures.** Skipping a malformed row
keeps one fat-fingered cell from denying service to every other customer.
Skipping a malformed *header* would be worse than useless: without the columns,
"no match" is indistinguishable from "unreadable", so that raises instead.

**An unparseable claim status is never guessed.** `parse_claim_status` returns
None and the row is skipped. Reading "approved" to a caller because a cell said
something we did not understand is the worst output this system could produce.

**The verification value has no field outside the repository.** `CustomerRow`
holds it; `Customer` does not. Secrets that do not exist in a type cannot leak
through a log line, a prompt or a serialised response.

## Decisions taken in Phase 1

**Configuration in exactly one module.** `core/config.py` is the only code that
reads the environment. Validation runs at import, so a bad `LOG_LEVEL` or
`ENVIRONMENT` stops the process at startup rather than failing mid-call.

**Dependencies resolved from app state, not module globals.** A route asks for
`SettingsDep`; `api/dependencies.py` reads the Settings that `create_app`
attached. Resolving through the cached `get_settings()` instead would mean an
app built with explicit settings quietly ran on the process-wide ones — which it
did, until a test caught it. The same module is where the session and
authenticated-caller dependencies belong (Phase 3), so the authorization boundary has one
enforcement point rather than one per route.

**Correlation ids in contextvars, not parameters.** A `request_id` (and, from
a `call_id`) is bound by middleware and read by the log formatter. No
function signature has to carry it, and no log line can forget it.

**Redaction at the formatter.** Sensitive field names are masked where records
are rendered, not at each call site, so a new log line cannot leak a phone number
by omission.

**One error envelope, context never serialised.** `AppError` carries a caller-safe
`message` plus a `context` dict that is logged and dropped from the response.
Upstream identifiers, submitted values and stack traces stay server-side.

**`IntegrationError` is distinct from `NotFoundError`.** Conflating them is the
specific failure mode the requirements call out: an unreachable spreadsheet must
never be reported to a caller as "we have no record of you". The type system
enforces the distinction from Phase 1, before either integration exists.

**Liveness separated from readiness.** `/health` never touches an external
system; `/ready` probes a registry of dependencies concurrently, with a timeout,
and treats a probe that raises as unhealthy rather than propagating a 500. The
registry is empty today, so registering the Phase 3 adapters extends readiness
without touching the endpoint.

**Retry policy configured before it is used.** Timeout, bounded retry count and
backoff base live in settings now so every integration client written later is
built against one budget rather than inventing its own.

## Deferred, and where it will go

| Phase | Work | Lands in |
| ----- | ---- | -------- |
| ~~3~~ | ~~Session state, authentication boundary, claim access~~ | ~~`services/`~~ — done |
| ~~4~~ | ~~`get_claim_status`, and the voice response layer~~ | ~~`tools/`~~ — done |
| 5 | `lookup_customer`, `verify_identity`, `search_faq`, `request_representative`, `complete_call` | `tools/` |
| 5 | FAQ content, kept out of the prompt | `knowledge/` |
| 5 | Prompts, turn handling, escalation and emergency routing | `agents/` |
| 5 | Voice platform webhook | `api/v1/` |
| ~~2~~ | ~~Customer/claim retrieval~~ | ~~`integrations/`~~ — done |
| ~~2~~ | ~~Retry-with-backoff helper, dependency registration for readiness~~ | ~~`core/`~~ — done |
| 5 | Post-call interaction persistence (Integration #2) | `integrations/` |
