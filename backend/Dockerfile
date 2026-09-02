# syntax=docker/dockerfile:1

# --- build stage -------------------------------------------------------------
# Dependencies are installed into a virtualenv that is copied into a clean
# runtime image, so build tooling never ships to production.
FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Copy only the metadata first so the dependency layer is cached across
# source-only changes.
COPY pyproject.toml ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && mkdir -p app && touch app/__init__.py \
    && /opt/venv/bin/pip install .

# --- runtime stage -----------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    ENVIRONMENT=prod \
    LOG_FORMAT=json \
    PORT=8000

# Run unprivileged.
RUN useradd --create-home --uid 1000 appuser

WORKDIR /srv

COPY --from=builder /opt/venv /opt/venv
COPY --chown=appuser:appuser app ./app

USER appuser

EXPOSE 8000

# Liveness only: readiness is polled by the orchestrator, not the container.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=2).status == 200 else 1)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
