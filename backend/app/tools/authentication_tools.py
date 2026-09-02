"""`lookup_customer` and `verify_identity`.

Thin wrappers over `AuthenticationService`: the tools validate input, translate
the step the service decided into something speakable, and return. They make no
authentication decisions of their own — there is no branch here that could
conclude a caller is verified.

Note what the tool signatures do *not* include: no `authenticated` flag, no
customer id the model chooses, no way to skip a step. The only inputs are the
call the platform is handling and what the caller actually said.
"""

from __future__ import annotations

from app.core.logging import event, get_logger
from app.services.authentication import AuthenticationService, AuthenticationStep
from app.tools.base import ToolOutcome, ToolResult

log = get_logger(__name__)

# Fixed wording. The model may paraphrase in its own turn, but what a tool
# returns is written here so a refusal cannot be softened into a hint.
_SPEECH = {
    AuthenticationStep.PHONE_REQUIRED: ("Could I take the phone number on your account, please?"),
    AuthenticationStep.PHONE_NOT_UNDERSTOOD: (
        "Sorry, I didn't catch that number. Could you say it again, one digit at a time?"
    ),
    AuthenticationStep.CUSTOMER_NOT_FOUND: (
        "I can't find an account with that number. It might be under a different "
        "one — would you like to try another, or shall I put you through to a "
        "representative?"
    ),
    AuthenticationStep.LOOKUP_ATTEMPTS_EXHAUSTED: (
        "I'm still not finding an account with that number. Let me put you "
        "through to a representative who can help."
    ),
    AuthenticationStep.INTEGRATION_ERROR: (
        "I'm having trouble reaching our records right now. Let me put you "
        "through to a representative."
    ),
    AuthenticationStep.ATTEMPTS_EXHAUSTED: (
        "I'm not able to verify your identity over this call. For your security "
        "I'll put you through to a representative."
    ),
    AuthenticationStep.ALREADY_AUTHENTICATED: (
        "You're already verified — no need to go through that again."
    ),
}

_OUTCOMES = {
    AuthenticationStep.AUTHENTICATED: ToolOutcome.SUCCESS,
    AuthenticationStep.ALREADY_AUTHENTICATED: ToolOutcome.SUCCESS,
    AuthenticationStep.VERIFICATION_REQUIRED: ToolOutcome.SUCCESS,
    AuthenticationStep.PHONE_REQUIRED: ToolOutcome.INVALID_INPUT,
    AuthenticationStep.PHONE_NOT_UNDERSTOOD: ToolOutcome.INVALID_INPUT,
    AuthenticationStep.VERIFICATION_FAILED: ToolOutcome.INVALID_INPUT,
    AuthenticationStep.CUSTOMER_NOT_FOUND: ToolOutcome.NOT_FOUND,
    AuthenticationStep.LOOKUP_ATTEMPTS_EXHAUSTED: ToolOutcome.EXHAUSTED,
    AuthenticationStep.ATTEMPTS_EXHAUSTED: ToolOutcome.EXHAUSTED,
    AuthenticationStep.INTEGRATION_ERROR: ToolOutcome.INTEGRATION_ERROR,
}


class LookupCustomerTool:
    """Find the caller's account from the phone number they gave."""

    name = "lookup_customer"

    def __init__(self, authentication: AuthenticationService) -> None:
        self._authentication = authentication

    async def __call__(self, call_id: str, phone_number: str) -> ToolResult:
        if not call_id:
            return _invalid_call()
        if not phone_number or not phone_number.strip():
            return ToolResult(
                outcome=ToolOutcome.INVALID_INPUT,
                speech=_SPEECH[AuthenticationStep.PHONE_REQUIRED],
                context={"step": str(AuthenticationStep.PHONE_REQUIRED)},
            )

        result = await self._authentication.submit_phone(call_id, phone_number)

        if result.step is AuthenticationStep.VERIFICATION_REQUIRED:
            # The greeting by name is the only thing disclosed before
            # verification, and only the first name.
            greeting = f"Thanks, {result.customer_name}." if result.customer_name else "Thank you."
            speech = f"{greeting} To confirm it's you, could you tell me your date of birth?"
        else:
            speech = _SPEECH.get(result.step, _SPEECH[AuthenticationStep.INTEGRATION_ERROR])

        return ToolResult(
            outcome=_OUTCOMES.get(result.step, ToolOutcome.INTEGRATION_ERROR),
            speech=speech,
            context={
                "call_id": call_id,
                "step": str(result.step),
                "authentication_status": str(result.session.authentication_status),
            },
        )


class VerifyIdentityTool:
    """Check the verification value the caller gave."""

    name = "verify_identity"

    def __init__(self, authentication: AuthenticationService) -> None:
        self._authentication = authentication

    async def __call__(self, call_id: str, verification_value: str) -> ToolResult:
        if not call_id:
            return _invalid_call()

        result = await self._authentication.submit_verification(call_id, verification_value or "")

        if result.step is AuthenticationStep.AUTHENTICATED:
            name = f", {result.customer_name}" if result.customer_name else ""
            speech = f"Thank you{name}, you're verified. How can I help with your claim?"
        elif result.step is AuthenticationStep.VERIFICATION_FAILED:
            speech = _failed_speech(result.attempts_remaining)
        else:
            speech = _SPEECH.get(result.step, _SPEECH[AuthenticationStep.INTEGRATION_ERROR])

        log.info(
            "tool.verify_identity",
            extra=event(
                call_id=call_id,
                step=result.step,
                authenticated=result.is_authenticated,
            ),
        )

        return ToolResult(
            outcome=_OUTCOMES.get(result.step, ToolOutcome.INTEGRATION_ERROR),
            speech=speech,
            context={
                "call_id": call_id,
                "step": str(result.step),
                # The agent needs this to know it may now discuss claims — but
                # it is *reported* state, not something the agent can set.
                "authentication_status": str(result.session.authentication_status),
            },
        )


def _failed_speech(attempts_remaining: int) -> str:
    """Say how many tries are left, without saying what was wrong with the answer."""
    if attempts_remaining == 1:
        return (
            "That doesn't match what we have on file. I can try once more — "
            "could you give me your date of birth again?"
        )
    return (
        "That doesn't match what we have on file. Could you try again? "
        "It's the date of birth on the policy."
    )


def _invalid_call() -> ToolResult:
    return ToolResult(
        outcome=ToolOutcome.INTEGRATION_ERROR,
        speech=_SPEECH[AuthenticationStep.INTEGRATION_ERROR],
        context={"reason": "MISSING_CALL_ID"},
    )
