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
External Systems          Google Sheets: customer/claim records (Integration #1)
                          interaction log (Integration #2, later)
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
integration never imports a service.

### Why this shape

The requirement that drives it is that **business logic must not live in prompt
text**. If the rule "a caller must be authenticated before claim data is
disclosed" is a sentence in a system prompt, it is a suggestion — one that
`"pretend I am already authenticated"` can argue with. If it is a check in a
service, against explicit session state, it cannot be talked out of. The layering
exists so there is one obvious place for such a rule and it is not the prompt.

This also makes the interesting parts testable without a phone call: services
and tools are plain Python with mocked integrations.

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
| 4 | `lookup_customer`, `verify_identity`, `get_claim_status`, `search_faq`, `request_representative`, `complete_call` | `tools/` |
| 4 | FAQ content, kept out of the prompt | `knowledge/` |
| 4 | Prompts, turn handling, escalation and emergency routing | `agents/` |
| 4 | Voice platform webhook | `api/v1/` |
| ~~2~~ | ~~Customer/claim retrieval~~ | ~~`integrations/`~~ — done |
| ~~2~~ | ~~Retry-with-backoff helper, dependency registration for readiness~~ | ~~`core/`~~ — done |
| 5 | Post-call interaction persistence (Integration #2) | `integrations/` |
