# Observe Insurance — VoiceAI Claims Support Agent

> **Status: complete.** All 25 mandatory requirements implemented, verified by
> 845 offline tests, and demonstrated live over a real voice call. Both bonus
> features are done.

---

## 1 · What this does

An inbound voice agent for insurance claim enquiries. A caller phones in, and
the agent:

- greets them, takes the phone number on their account, and looks them up
- verifies their identity before discussing anything specific
- reads back their claim status — and, when documents are outstanding, which
  ones and how to send them
- answers general questions: office hours, mailing address, starting a claim,
  how the process works
- hands them to a human whenever they ask, at any point
- recognises an emergency and stops everything else
- writes a record of the call to an external system when it ends

**The voice platform owns speech. This backend owns everything that must be
correct** — authorization, claim data, escalation records, post-call
persistence.

### The one idea worth knowing

**Authentication is enforced in code, never in prompt text.**

If "no claim data before verification" is a sentence in a system prompt, it is a
suggestion that *"pretend I'm already verified"* can argue with. Here it is a
check against explicit session state, run before the data source is touched.
The model cannot mark anyone verified because no mechanism to do so exists —
`get_claim_status` takes no identity argument, and the tool registry discards
any argument a tool did not declare.

The CLAUDE.md §7 bypass phrases are run through every caller-facing input as
tests. They are treated as what they are: wrong answers, which cost an attempt.

---

## 2 · Architecture

```
Caller
  │  PSTN / SIP
  ▼
Vapi                      speech-to-text, LLM turn-taking, text-to-speech
  │  HTTPS webhook + tool calls
  ▼
This backend
  ├── api/            HTTP transport only — parse, delegate, serialise
  ├── agents/         supervisor + 3 specialists; prompts. Decides what to *say*
  ├── tools/          5 narrow operations, each enforcing its own boundary
  ├── services/       the rules. The only layer that decides anything
  └── integrations/   repository contracts + adapters
        │
        ▼
Google Sheets   customer/claim records (read)   ·   interaction log (write)
```

Dependencies point one way, downward. A service never imports an agent; an
integration never imports a service.

| Layer | May depend on | Responsibility |
| ----- | ------------- | -------------- |
| `api` | services, schemas, core | Transport. No rules. |
| `agents` | tools, core | What to *say*. Never a business rule. |
| `tools` | services, schemas, core | Validate, enforce authorization, call one service |
| `services` | integrations, models, core | The rules |
| `integrations` | core | One external system each. No business logic. |

Design rationale — why each boundary sits where it does — is in
[docs/architecture.md](docs/architecture.md), organised by the phase that made
each decision.

### The five tools

That list is the entire surface the model can reach. No database access, no
generic API call, no escape hatch.

| Tool | Arguments | Before verification |
| ---- | --------- | ------------------- |
| `lookup_customer` | `phone_number` | yes |
| `verify_identity` | `verification_value` | yes |
| `get_claim_status` | *none* | **refuses** |
| `search_faq` | `question` | yes |
| `request_representative` | `reason`, `notes` | yes |

`get_claim_status` takes no arguments deliberately: the customer comes from the
authenticated session, so there is no parameter a model could aim at another
record.

### Multi-agent orchestration *(bonus)*

```
Supervisor
├── Claims Specialist      lookup_customer · verify_identity · get_claim_status
├── FAQ Specialist         search_faq
└── Escalation Handler     request_representative
```

Specialists own a **domain** — which tools belong to them — and nothing else.
Each delegates to the same registry the single-agent path used, so there is one
implementation of authentication, one of claim access, one of FAQ retrieval.

**Routing is deterministic, not a second model call.** A supervisor LLM would
add a round trip to every turn on a six-second budget, produce a second opinion
about an intent the assistant already resolved by choosing a tool, and make
"which specialist handled this" unreproducible. The supervisor derives intent
from the chosen tool instead.

It **cannot authenticate anyone** — it holds no session store, and routing to
the Claims Specialist does not authorise a claim. It is removable with one
argument: `ConversationService(supervisor=None)`, tested.

---

## 3 · Tech stack

| | | Why |
| --- | --- | --- |
| Python 3.13 | | |
| FastAPI + Uvicorn | | |
| Pydantic v2 / pydantic-settings | | Config validated at startup, not first use |
| httpx | | One async HTTP client for every outbound call |
| google-auth | | Service-account signing only — writes need it, API keys cannot write |
| pytest · ruff · mypy | dev | 845 tests, lint, format, types |

Five runtime dependencies. No Redis, no Kafka, no Celery, no vector database,
no ORM. Each was considered and left out — see [§10](#10--production-considerations).

Google Sheets is reached over its plain REST API rather than
`google-api-python-client`: one dependency instead of a discovery-document
stack, and an injectable transport that makes the whole integration testable
without credentials.

---

## 4 · Environment variables

All configuration is read in exactly one module,
[`core/config.py`](backend/app/core/config.py). Nothing else touches
`os.environ`. Invalid values fail at **startup**, not mid-call.

```bash
cp .env.example .env
```

Every setting is documented in [`.env.example`](.env.example). The ones that
matter:

| Variable | Required | Notes |
| -------- | -------- | ----- |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | for claims | Customer + claim data |
| `GOOGLE_SHEETS_API_KEY` | for claims | Read-only |
| `VOICE_PLATFORM_API_KEY` | **prod** | Webhook secret. The service *refuses to start* in staging/prod without it |
| `GOOGLE_INTERACTIONS_SPREADSHEET_ID` | for post-call | A **separate** sheet |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | for post-call | One line. API keys cannot write |
| `VOICE_TURN_BUDGET_SECONDS` | | 6s wall-clock cap on anything a caller waits through |

Secrets are `SecretStr`, so `repr(settings)` prints `**********` rather than a
live credential into a traceback.

**Blank means unset.** `.env.example` documents optional settings as `KEY=`;
copying it must not break startup.

Leave the Sheets variables blank and the service still runs — `/health` stays
green and `/ready` reports no dependencies. A credential problem is a visible
unavailable service, not a crash loop.

---

## 5 · Google Sheets setup

Two integrations, two spreadsheets — **deliberately separate files**:

| | #1 customers & claims | #2 interaction log |
| --- | --- | --- |
| Direction | read | **write** |
| Credential | API key | service account |
| Sharing | anyone with the link | the service account only |

The write credential must not be able to edit customer records — including the
`verification_value` column that authenticates callers. The service warns at
startup if both ids match.

Full walkthrough, including the sheet schemas and the demo data behind each
scenario: **[docs/google-sheets-setup.md](docs/google-sheets-setup.md)**

> All demo data is synthetic. Observe Insurance is fictional; every file in
> `knowledge/` says so in its own header.

---

## 6 · VoiceAI setup

Vapi. Setup takes about fifteen minutes:
**[docs/vapi-setup.md](docs/vapi-setup.md)**

All provider-specific code lives in one file,
[`integrations/voice_platform.py`](backend/app/integrations/voice_platform.py).
Everything above it speaks `VoiceEvent` and `ToolInvocation`, so swapping
platforms means rewriting that module and nothing else.

The assistant can be configured from the backend's own definitions, so prompt
and tool schemas cannot drift from the code that implements them:

```bash
curl -s <BASE_URL>/api/v1/voice/assistant-config | jq   # inspect
export BASE_URL=... VAPI_API_KEY=...
python scripts/create_vapi_assistant.py --create        # or create it directly
```

---

## 7 · How to run

Requires Python 3.11+ (developed on 3.13).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e "backend[dev]"
cp .env.example .env

cd backend && uvicorn app.main:app --reload --port 8000
```

```bash
curl -s localhost:8000/health   # {"status":"ok",...}
curl -s localhost:8000/ready    # dependencies, 503 if any is unhealthy
```

### Docker

```bash
docker build -f backend/Dockerfile -t observe-insurance-voice-ai .   # from the repo root
docker run --rm -p 8000:8000 --env-file .env observe-insurance-voice-ai
```

Two-stage build, non-root user, `HEALTHCHECK` on `/health`.

### Endpoints

| Method | Path | |
| ------ | ---- | --- |
| GET | `/health` | Liveness. Never touches an external system |
| GET | `/ready` | Readiness. Probes dependencies; 503 if any is unhealthy |
| GET | `/metrics` | Counters, latencies, derived rates. No identifiers, no secrets |
| POST | `/api/v1/voice/webhook` | Every voice event. Requires `x-vapi-secret` |
| GET | `/api/v1/voice/assistant-config` | Prompt + tool schemas, generated from code |
| GET | `/docs` | OpenAPI. Disabled in staging/prod |

Health probes sit **outside** `/api/v1` on purpose: they are an operational
contract for the load balancer, and must not move when the API version does.

---

## 8 · How to test

```bash
pytest backend/tests            # 845 tests
ruff check backend scripts      # lint
ruff format --check backend scripts
cd backend && mypy              # types (app/ only)
```

**No Google credentials, no network, anywhere.** External calls are mocked at
the HTTP transport, so retry policy, status handling and JSON parsing all run
for real — only the socket is fake.

Coverage includes: every authentication state transition and the attempt budget;
unauthorised claim access from each non-authenticated state; the CLAUDE.md §7
injection strings through every caller input; all five claim statuses; the
documents-required workflow; FAQ retrieval and refusal; emergency detection in
**both** directions; all fourteen failure modes at every external boundary;
post-call idempotency; and eight whole-call scenarios driven through the real
HTTP webhook.

Those last ones read as transcripts on purpose —
[`test_end_to_end_scenarios.py`](backend/tests/test_end_to_end_scenarios.py) can
be checked against CLAUDE.md by someone who has not read the implementation.

---

## 9 · Demo scenarios

**[docs/demo-scenarios.md](docs/demo-scenarios.md)** — exact scripts for:

| | Shows |
| --- | --- |
| 1 Happy path | greeting → lookup → verification → claim → completion → post-call record |
| 2 Authentication failure | three attempts, and proof no claim data was exposed |
| 3 Customer not found | distinct from failure; retry works |
| 4 Representative escalation | structured record, no workflow first |
| 5 Documents required | which documents, and how to send them |
| 6 **Debugging** | a controlled outage: structured error, safe response, retry, recovery |
| 7 FAQ + emergency | *"fire right now"* vs *"the fire last month"* |

Each says what to say, what to expect, and what to point at.

---

## 10 · Production considerations

### What is there

- **Timeouts and budgets.** Per-attempt timeout *plus* a 6-second wall-clock cap
  on anything a caller waits through — three attempts at ten seconds is thirty
  seconds of silence, by which point retrying is pointless.
- **Bounded retries** with exponential backoff and **full jitter**. Only
  transient failures; a 4xx is never retried.
- **One failure catalogue.** [`core/failures.py`](backend/app/core/failures.py)
  holds every failure mode; [FAILURE-MATRIX.md](docs/FAILURE-MATRIX.md) is
  generated from it, and tests fail the build if it drifts or if a new error
  code ships undocumented.
- **Structured logs.** JSON, event names as constants, `call_id` bound in a
  contextvar so one filter reconstructs a call. Phone numbers redacted at the
  formatter; the verification value has no field outside the repository.
- **Metrics** at `/metrics`: call duration, tool latency and outcomes,
  authentication success rate, escalation rate, post-call persistence rate.
- **Graceful degradation.** Runs without either integration configured. The
  webhook answers 200 even when handling fails — a 500 makes Vapi retry or drop
  the call, and a caller should not lose one to our bug.
- **Idempotency.** `call_id` checked in-process *and* against the sheet, so a
  redelivered webhook adds no second row.

### The rule everything is arranged around

**An infrastructure failure is never a business outcome.** A Sheets timeout is
not a customer who does not exist. They never share a classification, a log
level, or a sentence to a caller — asserted in both directions.

### Scaling

Everything is stateless **except sessions**, which live in process memory. Two
replicas would split one call's turns and lose the session.

To scale, in order of effort: **session affinity** (most platforms pin a call to
one backend — no new infrastructure, enough for several replicas) → **swap
`SessionStore` for Redis** (~40 lines, changes nothing else — the point of the
interface) → move idempotency with it → ship metrics somewhere.

### Deliberately not added

Redis, Kafka, Kubernetes, a vector database, a circuit breaker, distributed
tracing. None has a concrete need at one process, and CLAUDE.md §23 is explicit
that unnecessary distributed infrastructure is a cost rather than a credential.
The seams are in place so each can be added the day it is justified.

### Known limitations

Every one is recorded with its reasoning in
**[docs/DEFERRED.md](docs/DEFERRED.md)**. The ones that would matter first:

- Sessions are process-local — blocks multi-replica deployment
- The Sheets **read** credential is an API key, which needs the sheet
  link-shared. Fine for synthetic data, **not for real records**
- Transfer is implemented but unverified against a live account
- A permanently failed post-call write is logged, not automatically refiled
- Sentiment is outcome-derived, not tone analysis — we never see the audio

---

## Further reading

| | |
| --- | --- |
| [architecture.md](docs/architecture.md) | Why each boundary sits where it does |
| [demo-scenarios.md](docs/demo-scenarios.md) | Live demo scripts |
| [REQUIREMENTS-CHECKLIST.md](docs/REQUIREMENTS-CHECKLIST.md) | Every CLAUDE.md requirement, with evidence |
| [SECURITY-REVIEW.md](docs/SECURITY-REVIEW.md) | Secrets, logs, boundaries, injection, PII |
| [FAILURE-MATRIX.md](docs/FAILURE-MATRIX.md) | Generated from code |
| [DEFERRED.md](docs/DEFERRED.md) | What was left undone, and why |
| [CORE-COMPLETE.md](docs/CORE-COMPLETE.md) | The quality gate and live verification |
