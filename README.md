# Observe Insurance — VoiceAI Claims Support Agent

Backend for an inbound voice agent that handles insurance claim enquiries:
caller authentication, claim status, FAQ, representative escalation, emergency
handling and post-call record keeping.

The voice platform owns speech; this service owns the business logic,
authorization boundary and external integrations.

> **Status: Phase 2 complete — foundation + customer/claim data integration.**
> The service runs, and customer lookup, identity verification and claim
> retrieval work against Google Sheets. The conversation itself — session state,
> the authentication boundary, agent tools and the voice webhook — is not built
> yet. See [What is not built yet](#what-is-not-built-yet).

---

## Quick start

Requires Python 3.11+ (developed on 3.13).

```bash
git clone <repo> && cd observe-insurance-voice-ai

python3 -m venv .venv
source .venv/bin/activate

pip install -e "backend[dev]"

cp .env.example .env          # defaults work as-is for local development
```

The service runs without Google Sheets credentials: `/health` stays green and
`/ready` simply reports no dependencies. To exercise the data integration, follow
[docs/google-sheets-setup.md](docs/google-sheets-setup.md) and set
`GOOGLE_SHEETS_SPREADSHEET_ID` and `GOOGLE_SHEETS_API_KEY`.

Run the service:

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

Verify it:

```bash
curl -s localhost:8000/health | jq
# {"status":"ok","service":"observe-insurance-voice-ai","version":"0.1.0","environment":"local"}

curl -s localhost:8000/ready | jq
# {"status":"ready", ... ,"dependencies":[]}
```

Interactive API docs (non-production environments only): <http://localhost:8000/docs>

Run the tests:

```bash
pytest backend/tests            # from the repository root
```

No Google credentials are needed: every external call is mocked at the HTTP
transport.

Lint:

```bash
ruff check backend
```

---

## Docker

```bash
docker build -t observe-insurance-voice-ai ./backend
docker run --rm -p 8000:8000 --env-file .env observe-insurance-voice-ai
```

or:

```bash
docker compose up --build
```

The image is a two-stage build: dependencies are installed into a virtualenv
and copied into a clean `python:3.13-slim` runtime that runs as a non-root user.
`ENVIRONMENT=prod` and `LOG_FORMAT=json` are the image defaults, and a
`HEALTHCHECK` polls `/health`.

---

## Endpoints

| Method | Path            | Purpose                                                   |
| ------ | --------------- | --------------------------------------------------------- |
| GET    | `/health`       | Liveness. Process is up. Never touches external systems.  |
| GET    | `/ready`        | Readiness. Probes registered dependencies; 503 if any is unhealthy. |
| GET    | `/docs`         | OpenAPI UI. Disabled when `ENVIRONMENT` is `staging`/`prod`. |

Once Sheets is configured, `/ready` probes both repositories and returns 503 if
either the sheet is unreachable or a required column is missing.

Health probes deliberately sit **outside** the `/api/v1` prefix: they are an
operational contract for the load balancer and container orchestrator, so they
must not move when the product API version changes.

`/health` and `/ready` are separate on purpose. A liveness probe that consulted
Google Sheets would get the process **restarted** every time an upstream was
slow — exactly the wrong response. Liveness says "this process is alive";
readiness says "route traffic here".

---

## Project structure

```
observe-insurance-voice-ai/
├── backend/
│   ├── app/
│   │   ├── main.py              # app factory + lifespan; wiring only
│   │   ├── api/                 # HTTP transport
│   │   │   ├── dependencies.py  #   shared FastAPI dependencies
│   │   │   ├── health.py        #   liveness + readiness (unversioned)
│   │   │   ├── router.py        #   router assembly
│   │   │   └── v1/router.py     #   versioned product API (empty in Phase 1)
│   │   ├── agents/              # conversation orchestration        (Phase 2)
│   │   ├── tools/               # narrow agent-callable tools       (Phase 2)
│   │   ├── services/            # business logic                    (Phase 2)
│   │   ├── integrations/        # external-system adapters
│   │   │   ├── base.py          #   HealthCheckable / DependencyStatus contracts
│   │   │   ├── registry.py      #   readiness probe registry
│   │   │   ├── repositories.py  #   CustomerRepository / ClaimsRepository contracts
│   │   │   ├── factory.py       #   builds Integration #1 from settings
│   │   │   └── sheets/          #   Google Sheets adapters
│   │   │       ├── client.py    #     REST transport; no domain knowledge
│   │   │       ├── rows.py      #     row -> domain object, malformed-data policy
│   │   │       ├── customers.py #     GoogleSheetsCustomerRepository
│   │   │       └── claims.py    #     GoogleSheetsClaimsRepository
│   │   ├── schemas/             # request/response models (process boundary)
│   │   ├── models/             # Customer, Claim, controlled vocabularies
│   │   └── core/                # cross-cutting concerns
│   │       ├── config.py        #   pydantic-settings; the only env reader
│   │       ├── context.py       #   request/call id contextvars
│   │       ├── logging.py       #   JSON + console formatters, redaction
│   │       ├── middleware.py    #   correlation id + access log
│   │       ├── errors.py        #   AppError hierarchy
│   │       ├── phone.py         #   E.164 normalisation
│   │       ├── retry.py         #   bounded retry, backoff with jitter
│   │       └── exception_handlers.py
│   ├── tests/
│   ├── Dockerfile
│   └── pyproject.toml
├── docs/
│   ├── architecture.md
│   ├── google-sheets-setup.md   # sheet schema + setup
│   └── DEFERRED.md              # running ledger of deferred work
├── knowledge/                   # FAQ content, kept out of the prompt (Phase 3)
├── scripts/seed_data/           # demo customer + claim CSVs
├── docker-compose.yml
└── .env.example
```

---

## Configuration

All configuration is environment-driven and read in exactly one place,
`app/core/config.py`. Nothing else in the codebase touches `os.environ`.
Invalid values fail at startup rather than at first use — a bad `LOG_LEVEL`
stops the process immediately instead of surfacing mid-call.

See [.env.example](.env.example) for the full list. Secrets are never committed;
`.env` is git-ignored.

---

## Cross-cutting behaviour

**Correlation IDs.** `RequestContextMiddleware` binds a request id for the
lifetime of every request. An inbound `X-Request-ID` is honoured — so a trace the
voice platform started stays joined up — otherwise one is generated. The id is
echoed on the response, included in every log line, and returned in every error
body. A `call_id` contextvar is already in place for Phase 2 so that all logs
from one phone call can be filtered together.

**Structured logging.** One JSON object per line in deployment, a readable
single line locally (`LOG_FORMAT=console`). Log messages are *event names*
(`call.started`, `customer.lookup`, `authentication.failed`) with structured
fields, not prose — they are meant to be queried. Fields such as `caller_phone`
are redacted to the last four digits at the formatter, so redaction cannot be
forgotten at a call site. Uvicorn's own handlers are replaced so every line in
the process shares one format.

**Dependency injection.** Routes declare what they need via
`api/dependencies.py`; they never reach for a module-level global. Settings are
resolved from `app.state`, which `create_app` populates — so an app built with
explicit settings (a test, or an alternate entrypoint) is honoured instead of
silently falling back to the process-wide cache. Phase 2 adds the session and
authenticated-caller dependencies to the same module, which is what makes the
authorization boundary enforceable in one place.

**Error handling.** All failures leave through one of four handlers in
`core/exception_handlers.py` and share one response envelope:

```json
{"error": {"code": "INTEGRATION_ERROR", "message": "An upstream system is unavailable."},
 "request_id": "93747ea8d7e4487ea0050b6484e31dfe"}
```

`AppError` subclasses carry a stable `code`, an HTTP status, a caller-safe
`message`, and a `context` dict that is logged but never serialised into the
response — so spreadsheet ids, stack traces and submitted values cannot leak.
This is covered by tests.

---

## Integration #1 — customer and claim data

Business logic depends on two protocols in
[`integrations/repositories.py`](backend/app/integrations/repositories.py), never
on Google Sheets:

```python
class CustomerRepository(Protocol):
    async def lookup_customer_by_phone(self, phone_number: str) -> CustomerLookupResult: ...
    async def verify_customer(self, customer_id: str, verification_value: str) -> VerificationResult: ...

class ClaimsRepository(Protocol):
    async def get_claim_for_customer(self, customer_id: str) -> ClaimLookupResult: ...
```

Replacing the sheet with a real policy-administration API touches only
`integrations/sheets/` — no service, tool or agent.

**Outcomes are explicit, never inferred.** Each operation returns a structured
result carrying an outcome rather than raising for expected conditions, because
a phone call must not end when an upstream is slow:

```
CUSTOMER_FOUND · CUSTOMER_NOT_FOUND · INTEGRATION_ERROR
```

An integration failure is **never** reported as customer-not-found. A caller who
does hold a policy must never be told we have no record of them because a
spreadsheet was unreachable — that is the rule the whole module is arranged
around, and it is asserted directly in the tests.

A `FailureReason` sits underneath the outcome (`INVALID_PHONE_NUMBER`,
`UPSTREAM_TIMEOUT`, `MALFORMED_DATA`, …) so the agent can say "I didn't catch
that number" rather than "you have no account" — a materially different sentence
to hear on a support line — without blurring the three outcomes.

**Phone numbers are normalised on both sides of the match.** A caller says
`555-010-1234`, the sheet holds `+1 555 010 1234`; both become `+15550101234`.

**Failure handling.** Timeouts, 429s and 5xx are retried within a bounded budget
with exponentially backed-off, jittered delays; 4xx is not retried, since it
cannot succeed and only spends a live caller's patience. A malformed *row* is
skipped and logged by position so one bad record cannot deny service to everyone
else; a malformed *header* is an integration error, because a sheet we cannot
read must not be mistaken for a sheet with no matching customer.

See [docs/google-sheets-setup.md](docs/google-sheets-setup.md) for the schema,
setup steps and the demo data behind each mandatory scenario.

---

## Testing

```bash
pytest backend/tests
```

162 tests, all deterministic and offline — no Google credentials, no network.
External calls are mocked at the HTTP transport, so the client's URL building,
status handling, retry policy and JSON parsing all run for real and only the
socket is fake. Every test builds the app from an explicit `Settings` object, so
results never depend on the developer's `.env`.

Covered: phone normalisation across the forms a caller actually says; the retry
budget and its backoff schedule; successful and normalised lookup; customer not
found; malformed rows, missing columns and short rows; timeouts and upstream
failures never masquerading as not-found; successful and failed verification;
claim lookup including documents-required and most-recently-updated selection;
health and readiness; the error envelope and its non-leakage guarantees;
configuration validation; and log shape and redaction.

---

## What is not built yet

Phases 1–2 cover the foundation and the customer/claim data integration.
Deliberately **not** implemented yet:

- Session state and the authentication boundary that gates claim access
- The six agent tools and the services behind them (`services/`, `tools/` exist
  and are empty)
- Agent prompts, turn handling, escalation and emergency routing
- The voice platform webhook and any LLM orchestration
- FAQ knowledge base content
- Integration #2: post-call interaction persistence
- Service-account auth for Sheets (an API key covers Phase 2's reads; writes
  will need one)

Every deferral is tracked with a reason and a destination in
[docs/DEFERRED.md](docs/DEFERRED.md). The full requirement set is in
[CLAUDE.md](CLAUDE.md); the design intent behind the layering is in
[docs/architecture.md](docs/architecture.md).
