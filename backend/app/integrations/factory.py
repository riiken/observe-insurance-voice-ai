"""Building Integration #1 from configuration.

The one place that knows customer and claim data currently comes from Google
Sheets. Everything else asks for a `CustomerRepository` or a `ClaimsRepository`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.core.config import Settings
from app.core.errors import IntegrationError
from app.core.logging import event, get_logger
from app.integrations.registry import register_dependency
from app.integrations.repositories import (
    ClaimsRepository,
    CustomerRepository,
    InteractionRepository,
)
from app.integrations.sheets import (
    GoogleSheetsClaimsRepository,
    GoogleSheetsClient,
    GoogleSheetsCustomerRepository,
)
from app.integrations.sheets.auth import ApiKeyAuthorizer, ServiceAccountAuthorizer
from app.integrations.sheets.interactions import GoogleSheetsInteractionRepository

log = get_logger(__name__)


@dataclass(slots=True)
class DataIntegration:
    """The repositories, plus the clients whose connections need closing."""

    customers: CustomerRepository
    claims: ClaimsRepository
    _client: GoogleSheetsClient
    # Integration #2. Optional: post-call persistence is configured separately,
    # and a deployment without it still handles calls.
    interactions: InteractionRepository | None = None
    _interactions_client: GoogleSheetsClient | None = None

    async def aclose(self) -> None:
        await self._client.aclose()
        if self._interactions_client is not None:
            await self._interactions_client.aclose()


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
        authorizer=ApiKeyAuthorizer(str(settings.google_sheets_api_key)),
        base_url=settings.google_sheets_base_url,
        timeout_seconds=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
        backoff_base_seconds=settings.http_backoff_base_seconds,
        # A caller is on the line for every one of these reads.
        total_budget_seconds=settings.voice_turn_budget_seconds,
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

    interactions, interactions_client = _build_interactions(settings)

    log.info(
        "integration.configured",
        extra=event(
            integration="google-sheets",
            interactions=interactions is not None,
        ),
    )
    return DataIntegration(
        customers=customers,
        claims=claims,
        _client=client,
        interactions=interactions,
        _interactions_client=interactions_client,
    )


def _build_interactions(
    settings: Settings,
) -> tuple[InteractionRepository | None, GoogleSheetsClient | None]:
    """Wire Integration #2, or None when it is not configured.

    Never raises. A bad service-account key must not stop the service from
    answering calls — it costs us the interaction log, not the phone line.
    """
    if not settings.interactions_configured:
        log.warning("integration.not_configured", extra=event(integration="interactions"))
        return None, None

    if settings.interactions_share_the_customer_sheet:
        # Not fatal, but it means the write credential can edit customer rows.
        log.warning(
            "integration.shared_spreadsheet",
            extra=event(integration="interactions", risk="write_scope_on_customer_data"),
        )

    try:
        service_account = json.loads(str(settings.google_service_account_json))
        if not isinstance(service_account, dict):
            raise ValueError("service account JSON must be an object")
        authorizer = ServiceAccountAuthorizer(
            service_account,
            timeout_seconds=settings.http_timeout_seconds,
            token_endpoint=settings.google_token_endpoint,
        )
    except (ValueError, IntegrationError) as exc:
        log.error(
            "integration.credentials_invalid",
            extra=event(integration="interactions", cause=type(exc).__name__),
        )
        return None, None

    client = GoogleSheetsClient(
        spreadsheet_id=str(settings.google_interactions_spreadsheet_id),
        authorizer=authorizer,
        base_url=settings.google_sheets_base_url,
        # Nobody is waiting on a post-call write, so it gets the longer timeout
        # and the full attempt budget.
        timeout_seconds=settings.postcall_timeout_seconds,
        max_retries=settings.http_max_retries,
        backoff_base_seconds=settings.http_backoff_base_seconds,
    )
    repository = GoogleSheetsInteractionRepository(
        client, cell_range=settings.sheets_interactions_range
    )
    register_dependency(repository)
    return repository, client
