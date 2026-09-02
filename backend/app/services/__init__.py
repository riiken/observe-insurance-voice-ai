"""Business logic. Services own the rules; they are the only layer that decides.

The authentication boundary lives here, in Python, not in prompt text:
`AuthenticationService` moves a session between states only on a real result
from the customer repository, and `require_authenticated` is the single gate
every claim operation passes through.
"""

from app.services.authentication import (
    AuthenticationService,
    AuthenticationStep,
    AuthenticationStepResult,
)
from app.services.authorization import require_authenticated
from app.services.claims import ClaimsService, ClaimStatusResult
from app.services.guidance import ClaimGuidance, load_claim_guidance
from app.services.session_store import InMemorySessionStore, SessionStore

__all__ = [
    "AuthenticationService",
    "AuthenticationStep",
    "AuthenticationStepResult",
    "ClaimStatusResult",
    "ClaimsService",
    "ClaimGuidance",
    "InMemorySessionStore",
    "SessionStore",
    "load_claim_guidance",
    "require_authenticated",
]
