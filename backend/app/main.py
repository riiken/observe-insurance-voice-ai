"""Application factory and entrypoint.

Wiring only: configuration, logging, middleware, exception handlers, routes.
No business logic lives here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.router import register_routes
from app.core.config import Settings, get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging, event, get_logger
from app.core.middleware import register_middleware
from app.integrations.registry import clear_dependencies, registered_dependencies

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    log.info(
        "app.started",
        extra=event(
            environment=settings.environment,
            version=__version__,
            dependencies=[dep.name for dep in registered_dependencies()],
        ),
    )
    try:
        yield
    finally:
        # Phase 3 closes integration clients here.
        clear_dependencies()
        log.info("app.stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Accepts settings so tests can override them."""
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, log_format=settings.log_format)

    app = FastAPI(
        title="Observe Insurance VoiceAI Claims Support",
        description="Backend for the inbound claims support voice agent.",
        version=__version__,
        lifespan=lifespan,
        # API docs are useful in development but are not exposed in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.settings = settings

    register_middleware(app)
    register_exception_handlers(app)
    register_routes(app, api_prefix=settings.api_prefix)

    return app


app = create_app()
