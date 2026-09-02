"""Assembling the service layer.

Built once at startup and hung off application state. Services are stateless
apart from the session store they share, so one instance each is correct.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.integrations.factory import DataIntegration
from app.services.authentication import AuthenticationService
from app.services.claims import ClaimsService
from app.services.guidance import ClaimGuidance, load_claim_guidance
from app.services.session_store import InMemorySessionStore, SessionStore
from app.tools.claim_status import ClaimStatusTool


@dataclass(frozen=True, slots=True)
class ServiceContainer:
    """Everything the conversation layer will ask for."""

    sessions: SessionStore
    authentication: AuthenticationService
    claims: ClaimsService
    guidance: ClaimGuidance
    claim_status_tool: ClaimStatusTool


def build_services(
    integration: DataIntegration, *, guidance_path: Path | None = None
) -> ServiceContainer:
    """Wire the services and tools onto Integration #1.

    Takes the integration rather than settings: a service layer that cannot
    reach its data has nothing useful to offer, so the dependency is required
    rather than optional. When the integration is absent, no container is built
    and the API dependency reports the service as unavailable.

    Claim guidance is loaded here, at startup. If it is missing or incomplete
    the process fails to start — which is the right moment to find out, because
    the alternative is an agent with nothing configured to say improvising
    about a customer's claim mid-call.
    """
    sessions = InMemorySessionStore()
    guidance = load_claim_guidance(guidance_path)
    claims = ClaimsService(integration.claims, sessions)

    return ServiceContainer(
        sessions=sessions,
        authentication=AuthenticationService(integration.customers, sessions),
        claims=claims,
        guidance=guidance,
        claim_status_tool=ClaimStatusTool(claims, guidance),
    )
