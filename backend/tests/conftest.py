"""Shared fixtures.

Every test builds the app from an explicit Settings object so results never
depend on the developer's `.env` or exported environment.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.integrations.registry import clear_dependencies
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        # _env_file=None keeps a developer's local .env out of the test run.
        _env_file=None,
        environment="local",
        debug=True,
        log_level="DEBUG",
        log_format="console",
        voice_platform_api_key=None,
    )


@pytest.fixture(autouse=True)
def _clean_dependency_registry() -> Iterator[None]:
    """The readiness registry is process-global; keep tests isolated."""
    clear_dependencies()
    yield
    clear_dependencies()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # `with` runs lifespan, so startup/shutdown are exercised by every test.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
