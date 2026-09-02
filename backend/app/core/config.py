"""Environment-driven application configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
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

    # Wall-clock ceiling for anything a caller is waiting through. Three
    # attempts at ten seconds is thirty seconds of silence on a phone call, by
    # which point retrying is pointless — the caller has gone. Failing fast
    # leaves time to apologise and offer a person.
    voice_turn_budget_seconds: float = Field(default=6.0, gt=0)

    # Post-call writes have no caller waiting, so they may take longer and use
    # the full attempt budget.
    postcall_timeout_seconds: float = Field(default=20.0, gt=0)

    # How long a readiness result may be reused. Orchestrator probes are
    # frequent and each one reads every sheet; a few seconds of staleness costs
    # far less than the upstream quota an unthrottled probe loop consumes.
    # Zero disables the cache.
    readiness_cache_seconds: float = Field(default=5.0, ge=0)

    # --- Google Sheets (Integration #1: customer + claim retrieval) --------
    # An API key reads a link-shared sheet, which is all Phase 2 needs. Writes
    # (Integration #2) will need a service account; see docs/DEFERRED.md.
    # SecretStr, not str: pydantic masks these in `repr`, so a traceback frame
    # or an accidental log of the settings object prints "**********" rather
    # than a live credential.
    google_sheets_api_key: SecretStr | None = None
    google_sheets_spreadsheet_id: str | None = None
    google_sheets_base_url: str = "https://sheets.googleapis.com/v4/spreadsheets"
    sheets_customers_range: str = "Customers!A:D"
    sheets_claims_range: str = "Claims!A:E"

    # --- Google Sheets (Integration #2: post-call interaction records) ------
    # A separate spreadsheet, written with a service account. An API key cannot
    # write, and the write credential must not be able to edit customer data —
    # so this is its own file with its own sharing.
    google_interactions_spreadsheet_id: str | None = None
    # The service account JSON key, as a single-line JSON string.
    google_service_account_json: SecretStr | None = None
    sheets_interactions_range: str = "Interactions!A:L"
    # Override only to point at a stub during local testing.
    google_token_endpoint: str = "https://oauth2.googleapis.com/token"

    # --- Knowledge ---------------------------------------------------------
    # Configured claim guidance. None resolves to knowledge/claim_guidance.json
    # at the repository root; override to point at a different content set.
    claim_guidance_path: Path | None = None
    # Directory of FAQ knowledge files (one Markdown file per topic).
    # None resolves to knowledge/ at the repository root.
    faq_directory: Path | None = None
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
    voice_platform_api_key: SecretStr | None = None

    # Where a representative request hands the call. Unset means the platform
    # cannot transfer, and the realistic escalation workflow runs instead — the
    # escalation record then says REQUESTED rather than claiming a transfer we
    # did not perform.
    voice_transfer_phone_number: str | None = None

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
        if self.is_production and not self.secret(self.voice_platform_api_key):
            raise ValueError(
                "VOICE_PLATFORM_API_KEY is required when ENVIRONMENT is staging or prod: "
                "an unauthenticated webhook would expose the tool layer."
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment in ("staging", "prod")

    def secret(self, value: SecretStr | None) -> str | None:
        """Unwrap a secret at the one point it is actually used."""
        return value.get_secret_value() if value is not None else None

    @property
    def interactions_configured(self) -> bool:
        """Whether Integration #2 can write.

        Both halves are required: a spreadsheet to write to, and a service
        account to write with. Absent either, calls still complete and the
        record is logged rather than filed.
        """
        return bool(self.google_interactions_spreadsheet_id and self.google_service_account_json)

    @property
    def interactions_share_the_customer_sheet(self) -> bool:
        """True when the write credential would also reach customer data."""
        return bool(
            self.google_interactions_spreadsheet_id
            and self.google_interactions_spreadsheet_id == self.google_sheets_spreadsheet_id
        )

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
