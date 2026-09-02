# Observe Insurance — VoiceAI Claims Support Agent

Backend for an inbound voice agent that handles insurance claim enquiries:
caller authentication, claim status, FAQ, representative escalation, emergency
handling and post-call record keeping.

The voice platform owns speech; this service owns the business logic,
authorization boundary and external integrations.

> **Status: Phase 1 — foundation only.**
> The runnable service, configuration, logging, error handling, health probes
> and test harness are in place. The claims workflow itself is not yet
> implemented — see [What is not built yet](#what-is-not-built-yet).

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
│   │   │   └── registry.py      #   readiness probe registry
│   │   ├── schemas/             # request/response models (process boundary)
│   │   ├── models/enums.py      # controlled domain vocabularies
│   │   └── core/                # cross-cutting concerns
│   │       ├── config.py        #   pydantic-settings; the only env reader
│   │       ├── context.py       #   request/call id contextvars
│   │       ├── logging.py       #   JSON + console formatters, redaction
│   │       ├── middleware.py    #   correlation id + access log
│   │       ├── errors.py        #   AppError hierarchy
│   │       └── exception_handlers.py
│   ├── tests/
│   ├── Dockerfile
│   └── pyproject.toml
├── docs/architecture.md
├── knowledge/                   # FAQ content, kept out of the prompt (Phase 2)
├── scripts/                     # demo/seed scripts                 (Phase 3)
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

## Testing

```bash
pytest backend/tests
```

39 tests, all deterministic and offline. Every test builds the app from an
explicit `Settings` object, so results never depend on the developer's `.env`.
The `TestClient` is entered as a context manager, so application startup and
shutdown run in every test.

Current coverage: health and readiness (including a dependency whose probe
raises), settings injection, correlation-id propagation and isolation, the error
envelope and its non-leakage guarantees, configuration validation, and log
shape/redaction.

---

## What is not built yet

Phase 1 is foundation only. Deliberately **not** implemented:

- The claims conversation workflow, session state and authentication boundary
- Agent, tool and service implementations (the packages exist and are empty)
- Google Sheets integrations for customer/claim lookup and post-call records
- FAQ knowledge base content
- The voice platform webhook and any LLM orchestration
- Escalation and emergency handling
- Retry/backoff execution (the budget is configured; the helper is not written)

The full requirement set is in [CLAUDE.md](CLAUDE.md); the design intent behind
the layering is in [docs/architecture.md](docs/architecture.md).
