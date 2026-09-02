"""Assembling the service layer.

Built once at startup and hung off application state. Services are stateless
apart from the session store they share, so one instance each is correct.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.integrations.factory import DataIntegration
from app.services.authentication import AuthenticationService
from app.services.claims import ClaimsService
from app.services.session_store import InMemorySessionStore, SessionStore


@dataclass(frozen=True, slots=True)
class ServiceContainer:
    """Everything the conversation layer will ask for."""

    sessions: SessionStore
    authentication: AuthenticationService
    claims: ClaimsService


def build_services(integration: DataIntegration) -> ServiceContainer:
    """Wire the services onto Integration #1.

    Takes the integration rather than settings: a service layer that cannot
    reach its data has nothing useful to offer, so the dependency is required
    rather than optional. When the integration is absent, no container is built
    and the API dependency reports the service as unavailable.
    """
    sessions = InMemorySessionStore()

    return ServiceContainer(
        sessions=sessions,
        authentication=AuthenticationService(integration.customers, sessions),
        claims=ClaimsService(integration.claims, sessions),
    )
