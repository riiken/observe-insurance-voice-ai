"""Shared FastAPI dependencies.

One place for everything injected into routes, so a route declares what it needs
instead of reaching for a module-level global.

Note what is *not* here: there is no "current authenticated caller" dependency.
Authorization is not established by a route's dependency graph but inside the
service, from the session the `call_id` identifies. A route cannot accidentally
be written without the check, because the route never performs it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.core.errors import ServiceUnavailableError
from app.services.authentication import AuthenticationService
from app.services.claims import ClaimsService
from app.services.container import ServiceContainer


def get_app_settings(request: Request) -> Settings:
    """Resolve the settings the running application was built with.

    `create_app` attaches its Settings to app state, so an explicitly configured
    app — tests, or an alternate entrypoint — is honoured. Falling back to the
    process-wide cache would silently ignore those settings.
    """
    settings: Settings | None = getattr(request.app.state, "settings", None)
    return settings if settings is not None else get_settings()


def get_services(request: Request) -> ServiceContainer:
    """The service layer, or a 503 when the data integration is not configured.

    Degrading here rather than at startup is deliberate: the process stays up
    and `/health` stays green, so a credential problem is a visible unavailable
    service rather than a crash loop.
    """
    services: ServiceContainer | None = getattr(request.app.state, "services", None)
    if services is None:
        raise ServiceUnavailableError(
            "Claims support is temporarily unavailable.", reason="integration_not_configured"
        )
    return services


def get_authentication_service(
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> AuthenticationService:
    return services.authentication


def get_claims_service(
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> ClaimsService:
    return services.claims


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
ServicesDep = Annotated[ServiceContainer, Depends(get_services)]
AuthenticationServiceDep = Annotated[AuthenticationService, Depends(get_authentication_service)]
ClaimsServiceDep = Annotated[ClaimsService, Depends(get_claims_service)]
