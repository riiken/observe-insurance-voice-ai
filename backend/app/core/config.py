"""Environment-driven application configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "dev", "staging", "prod"]
LogFormat = Literal["json", "console"]


class Settings(BaseSettings):
    """All runtime configuration. Nothing is read from os.environ outside this class."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application -------------------------------------------------------
    app_name: str = "observe-insurance-voice-ai"
    environment: Environment = "local"
    debug: bool = False
    api_prefix: str = "/api/v1"

    # --- Server ------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000

    # --- Logging -----------------------------------------------------------
    log_level: str = "INFO"
    log_format: LogFormat = "json"

    # --- Outbound integration defaults -------------------------------------
    # Concrete integrations land in Phase 3; the policy knobs live here now so
    # every client is built with the same timeout/retry budget.
    http_timeout_seconds: float = Field(default=10.0, gt=0)
    http_max_retries: int = Field(default=2, ge=0, le=5)
    http_backoff_base_seconds: float = Field(default=0.2, gt=0)

    # --- Google Sheets (Integration #1: customer + claim retrieval) --------
    # An API key reads a link-shared sheet, which is all Phase 2 needs. Writes
    # (Integration #2) will need a service account; see docs/DEFERRED.md.
    google_sheets_api_key: str | None = None
    google_sheets_spreadsheet_id: str | None = None
    google_sheets_base_url: str = "https://sheets.googleapis.com/v4/spreadsheets"
    sheets_customers_range: str = "Customers!A:D"
    sheets_claims_range: str = "Claims!A:E"

    # --- Knowledge ---------------------------------------------------------
    # Configured claim guidance. None resolves to knowledge/claim_guidance.json
    # at the repository root; override to point at a different content set.
    claim_guidance_path: Path | None = None
    # Supported FAQ answers. None resolves to knowledge/faq.json.
    faq_path: Path | None = None
    # The agent's system prompt. None resolves to the file shipped with the app.
    system_prompt_path: Path | None = None

    # --- Telephony ---------------------------------------------------------
    # Applied to national-format numbers a caller reads out without a country code.
    default_phone_country_code: str = "+1"

    # --- Security ----------------------------------------------------------
    # Shared secret the voice platform presents on webhook calls (Vapi sends it
    # as the `x-vapi-secret` header). Unset in local development means the check
    # is skipped; a production environment refuses to start without it, because
    # an unauthenticated webhook is an open door to the tool layer.
    voice_platform_api_key: str | None = None

    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        level = value.upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        if level not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return level

    @field_validator("default_phone_country_code")
    @classmethod
    def _validate_country_code(cls, value: str) -> str:
        code = value.strip()
        if not code.startswith("+") or not code[1:].isdigit():
            raise ValueError("default_phone_country_code must look like '+1'")
        return code

    @model_validator(mode="after")
    def _require_webhook_secret_in_production(self) -> Settings:
        if self.is_production and not self.voice_platform_api_key:
            raise ValueError(
                "VOICE_PLATFORM_API_KEY is required when ENVIRONMENT is staging or prod: "
                "an unauthenticated webhook would expose the tool layer."
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment in ("staging", "prod")

    @property
    def sheets_configured(self) -> bool:
        """Whether Integration #1 has enough configuration to be wired up.

        Absent configuration is not an error: the service still starts, and
        readiness simply reports the integration as unconfigured rather than
        the process failing to boot.
        """
        return bool(self.google_sheets_api_key and self.google_sheets_spreadsheet_id)


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor used as a FastAPI dependency."""
    return Settings()
