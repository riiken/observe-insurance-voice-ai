"""Assembling the service layer.

Built once at startup and hung off application state. Services are stateless
apart from the session store they share, so one instance each is correct.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.agents.prompt import load_system_prompt
from app.agents.specialists import Supervisor
from app.integrations.factory import DataIntegration
from app.integrations.voice_platform import supports_transfer
from app.services.authentication import AuthenticationService
from app.services.claims import ClaimsService
from app.services.conversation import ConversationService
from app.services.escalation import EscalationService
from app.services.faq import FaqService, load_faq_entries
from app.services.guidance import ClaimGuidance, load_claim_guidance
from app.services.postcall import PostCallService
from app.services.safety import SafetyService
from app.services.session_store import InMemorySessionStore, SessionStore
from app.tools.authentication_tools import LookupCustomerTool, VerifyIdentityTool
from app.tools.claim_status import ClaimStatusTool
from app.tools.faq_tool import SearchFaqTool
from app.tools.registry import ToolRegistry, build_registry
from app.tools.representative_tool import RequestRepresentativeTool
from app.tools.safety_interceptor import EmergencyInterceptor


@dataclass(frozen=True, slots=True)
class ServiceContainer:
    """Everything the conversation layer will ask for."""

    sessions: SessionStore
    authentication: AuthenticationService
    claims: ClaimsService
    escalation: EscalationService
    faq: FaqService
    safety: SafetyService
    postcall: PostCallService
    conversation: ConversationService
    guidance: ClaimGuidance
    tools: ToolRegistry
    supervisor: Supervisor
    system_prompt: str
    claim_status_tool: ClaimStatusTool


def build_services(
    integration: DataIntegration,
    *,
    guidance_path: Path | None = None,
    faq_directory: Path | None = None,
    prompt_path: Path | None = None,
    transfer_to: str | None = None,
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

    # All configured content is read here, at startup. Missing or incomplete
    # content fails the process rather than surfacing mid-call.
    guidance = load_claim_guidance(guidance_path)
    faq = FaqService(load_faq_entries(faq_directory))
    system_prompt = load_system_prompt(prompt_path)

    authentication = AuthenticationService(integration.customers, sessions)
    claims = ClaimsService(integration.claims, sessions)
    safety = SafetyService()
    escalation = EscalationService(sessions, transfer_available=supports_transfer(transfer_to))
    postcall = PostCallService(integration.interactions)

    claim_status_tool = ClaimStatusTool(claims, guidance)

    # The five tools the agent gets. Nothing else is reachable from a webhook.
    # The interceptor runs ahead of all of them, so an emergency described in
    # answer to any question takes over the turn.
    tools = build_registry(
        lookup_customer=LookupCustomerTool(authentication),
        verify_identity=VerifyIdentityTool(authentication),
        get_claim_status=claim_status_tool,
        search_faq=SearchFaqTool(faq, sessions),
        request_representative=RequestRepresentativeTool(
            escalation, safety, transfer_to=transfer_to
        ),
        safety=EmergencyInterceptor(safety, escalation, transfer_to=transfer_to),
    )

    # One supervisor over the same registry: the specialists route to the very
    # tools the single-agent path used, so there is no second implementation of
    # anything for them to disagree about.
    supervisor = Supervisor(tools)

    return ServiceContainer(
        sessions=sessions,
        authentication=authentication,
        claims=claims,
        escalation=escalation,
        faq=faq,
        safety=safety,
        postcall=postcall,
        conversation=ConversationService(
            authentication=authentication,
            sessions=sessions,
            tools=tools,
            postcall=postcall,
            supervisor=supervisor,
        ),
        guidance=guidance,
        tools=tools,
        supervisor=supervisor,
        system_prompt=system_prompt,
        claim_status_tool=claim_status_tool,
    )
