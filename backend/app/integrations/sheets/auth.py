"""Authorising Google Sheets calls.

Two mechanisms, one interface:

- **API key** — read-only, and only against a link-shared sheet. Everything
  Integration #1 needs, with no credential to rotate.
- **Service account** — required for writes. Integration #2 appends interaction
  records, and an API key simply cannot do that.

The service account signs its own JWT assertion and exchanges it over our httpx
client, rather than going through `google-api-python-client`. That keeps one
transport for every outbound call, keeps the whole thing mockable without
credentials, and avoids a second HTTP stack in the image.

**Give the write credential its own spreadsheet.** A service account with write
scope on the customer sheet can edit customer records. `Settings` warns when the
interactions sheet is the same file as the customer data.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol, runtime_checkable

import httpx

from app.core.errors import IntegrationError
from app.core.logging import event, get_logger

log = get_logger(__name__)

INTEGRATION_NAME = "google-sheets"

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"

READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
READWRITE_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

# Tokens last an hour; refresh early so a call never stalls on an expiry race.
_REFRESH_MARGIN_SECONDS = 300
_ASSERTION_LIFETIME_SECONDS = 3600


@runtime_checkable
class SheetsAuthorizer(Protocol):
    """Supplies whatever a request needs to be accepted."""

    name: str

    async def credentials(self) -> tuple[dict[str, str], dict[str, str]]:
        """Return (headers, query parameters) to attach to the request."""
        ...


class ApiKeyAuthorizer:
    """Read-only access to a link-shared sheet."""

    name = "api_key"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def credentials(self) -> tuple[dict[str, str], dict[str, str]]:
        return {}, {"key": self._api_key}


class ServiceAccountAuthorizer:
    """OAuth via the JWT-bearer flow, with a cached access token."""

    name = "service_account"

    def __init__(
        self,
        service_account_info: dict[str, Any],
        *,
        scope: str = READWRITE_SCOPE,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
        token_endpoint: str = TOKEN_ENDPOINT,
    ) -> None:
        missing = [
            field
            for field in ("client_email", "private_key")
            if not service_account_info.get(field)
        ]
        if missing:
            raise IntegrationError(
                "Service account credentials are incomplete.",
                integration=INTEGRATION_NAME,
                missing_fields=missing,
            )

        self._info = service_account_info
        self._scope = scope
        self._token_endpoint = token_endpoint
        self._token: str | None = None
        self._expires_at = 0.0
        # One refresh at a time: a burst of calls on a cold cache should
        # produce one token exchange, not one per call.
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=timeout_seconds, transport=transport)

    async def credentials(self) -> tuple[dict[str, str], dict[str, str]]:
        return {"Authorization": f"Bearer {await self._access_token()}"}, {}

    async def _access_token(self) -> str:
        if self._token is not None and time.time() < self._expires_at:
            return self._token

        async with self._lock:
            # Another waiter may have refreshed while we queued.
            if self._token is not None and time.time() < self._expires_at:
                return self._token

            token, expires_in = await self._exchange_assertion()
            self._token = token
            self._expires_at = time.time() + max(0, expires_in - _REFRESH_MARGIN_SECONDS)
            log.info("sheets.token_refreshed", extra=event(expires_in=expires_in))
            return token

    async def _exchange_assertion(self) -> tuple[str, int]:
        assertion = await asyncio.to_thread(self._build_assertion)

        try:
            response = await self._client.post(
                self._token_endpoint,
                data={"grant_type": _JWT_BEARER_GRANT, "assertion": assertion},
            )
        except httpx.TimeoutException as exc:
            raise IntegrationError(
                "Timed out obtaining a Google access token.",
                integration=INTEGRATION_NAME,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise IntegrationError(
                "Could not reach the Google token endpoint.",
                integration=INTEGRATION_NAME,
                cause=type(exc).__name__,
                retryable=True,
            ) from exc

        if response.status_code != httpx.codes.OK:
            # A 400 here is a bad key or a wrong scope: retrying will not help.
            raise IntegrationError(
                "Google rejected the service account credentials.",
                integration=INTEGRATION_NAME,
                # Diagnostic, not our HTTP status — see the note in client.py.
                upstream_status=response.status_code,
                retryable=response.status_code >= 500,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise IntegrationError(
                "Google returned an unreadable token response.",
                integration=INTEGRATION_NAME,
            ) from exc

        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise IntegrationError("Google returned no access token.", integration=INTEGRATION_NAME)

        expires_in = payload.get("expires_in")
        return token, int(expires_in) if isinstance(expires_in, (int, float)) else 3600

    def _build_assertion(self) -> str:
        """Sign the JWT. Blocking (RSA), so callers run it off the event loop."""
        from google.auth import crypt, jwt  # imported lazily: signing only

        issued_at = int(time.time())
        signer = crypt.RSASigner.from_service_account_info(self._info)
        return jwt.encode(
            signer,
            {
                "iss": self._info["client_email"],
                "scope": self._scope,
                "aud": self._token_endpoint,
                "iat": issued_at,
                "exp": issued_at + _ASSERTION_LIFETIME_SECONDS,
            },
        ).decode("utf-8")

    async def aclose(self) -> None:
        await self._client.aclose()
