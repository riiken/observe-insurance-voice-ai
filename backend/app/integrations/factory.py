"""Building Integration #1 from configuration.

The one place that knows customer and claim data currently comes from Google
Sheets. Everything else asks for a `CustomerRepository` or a `ClaimsRepository`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.core.logging import event, get_logger
from app.integrations.registry import register_dependency
from app.integrations.repositories import ClaimsRepository, CustomerRepository
from app.integrations.sheets import (
    GoogleSheetsClaimsRepository,
    GoogleSheetsClient,
    GoogleSheetsCustomerRepository,
)

log = get_logger(__name__)


@dataclass(slots=True)
class DataIntegration:
    """The repositories, plus the client whose connections need closing."""

    customers: CustomerRepository
    claims: ClaimsRepository
    _client: GoogleSheetsClient

    async def aclose(self) -> None:
        await self._client.aclose()


def build_data_integration(settings: Settings) -> DataIntegration | None:
    """Wire up Integration #1, or None when it is not configured.

    Missing configuration is not a startup failure. The service still boots and
    serves `/health`; `/ready` reports the integration as absent. That is what
    lets the app be run and inspected without a Google account, and it keeps a
    credential problem from turning into a crash loop.
    """
    if not settings.sheets_configured:
        log.warning("integration.not_configured", extra=event(integration="google-sheets"))
        return None

    client = GoogleSheetsClient(
        # Narrowed by `sheets_configured`.
        spreadsheet_id=str(settings.google_sheets_spreadsheet_id),
        api_key=str(settings.google_sheets_api_key),
        base_url=settings.google_sheets_base_url,
        timeout_seconds=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
        backoff_base_seconds=settings.http_backoff_base_seconds,
    )

    customers = GoogleSheetsCustomerRepository(
        client,
        cell_range=settings.sheets_customers_range,
        default_country_code=settings.default_phone_country_code,
    )
    claims = GoogleSheetsClaimsRepository(client, cell_range=settings.sheets_claims_range)

    # Readiness now reflects the sheet being reachable and correctly shaped —
    # the hook Phase 1 left open.
    register_dependency(customers)
    register_dependency(claims)

    log.info("integration.configured", extra=event(integration="google-sheets"))
    return DataIntegration(customers=customers, claims=claims, _client=client)
