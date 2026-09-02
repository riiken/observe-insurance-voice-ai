"""Environment-driven configuration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


def _settings(**overrides) -> Settings:
    # _env_file=None keeps the developer's local .env out of these assertions.
    return Settings(_env_file=None, **overrides)


def test_defaults_are_safe_for_local_development() -> None:
    settings = _settings()

    assert settings.environment == "local"
    assert settings.debug is False
    assert settings.log_format == "json"
    assert settings.is_production is False


def test_values_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("LOG_LEVEL", "warning")
    monkeypatch.setenv("PORT", "9001")
    monkeypatch.setenv("VOICE_PLATFORM_API_KEY", "secret")  # required in prod

    settings = _settings()

    assert settings.environment == "prod"
    assert settings.log_level == "WARNING"  # normalised
    assert settings.port == 9001
    assert settings.is_production is True


def test_invalid_log_level_is_rejected_at_startup() -> None:
    with pytest.raises(ValidationError):
        _settings(log_level="chatty")


def test_invalid_environment_is_rejected_at_startup() -> None:
    with pytest.raises(ValidationError):
        _settings(environment="somewhere-else")


def test_retry_budget_is_bounded() -> None:
    with pytest.raises(ValidationError):
        _settings(http_max_retries=50)


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


@pytest.mark.parametrize("environment", ["staging", "prod"])
def test_production_refuses_to_start_without_a_webhook_secret(environment: str) -> None:
    """An unauthenticated webhook is an open door to the tool layer."""
    with pytest.raises(ValidationError):
        _settings(environment=environment)


@pytest.mark.parametrize("environment", ["staging", "prod"])
def test_production_starts_once_the_secret_is_set(environment: str) -> None:
    assert _settings(environment=environment, voice_platform_api_key="s").is_production


def test_local_development_does_not_need_a_webhook_secret() -> None:
    """Requiring one locally would mean nobody could run the service to look at it."""
    assert _settings(environment="local").voice_platform_api_key is None
