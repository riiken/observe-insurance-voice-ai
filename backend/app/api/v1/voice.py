"""Voice platform webhook.

Transport only. The route authenticates the request, hands the payload to the
provider adapter to translate, passes the resulting `VoiceEvent` to
`ConversationService`, and formats the reply. No business logic, and no
knowledge of what any particular event means.

Always answers 200 for an authenticated request, even when handling failed. A
webhook that returns 500 makes Vapi retry or drop the call; a caller should not
lose a phone call because a tool raised.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse

from app.api.dependencies import ServicesDep, SettingsDep, get_services
from app.core.logging import event, get_logger
from app.integrations.voice_platform import (
    PLATFORM_NAME,
    SECRET_HEADER,
    VoiceEventType,
    acknowledgement,
    format_tool_results,
    parse_webhook,
    tool_schemas,
    verify_secret,
)

router = APIRouter(prefix="/voice", tags=["voice"])

log = get_logger(__name__)


@router.post("/webhook", summary="Voice platform webhook")
async def voice_webhook(
    request: Request,
    settings: SettingsDep,
    x_vapi_secret: Annotated[str | None, Header(alias=SECRET_HEADER)] = None,
) -> JSONResponse:
    """Receive every event for a call: start, tool calls, completion.

    The service layer is resolved inside the handler rather than as a
    dependency, so the secret is checked *first*. Resolving it in the signature
    would let an unauthenticated caller learn whether the integration is
    configured, by telling 503 apart from 401.
    """
    if not verify_secret(x_vapi_secret, settings.voice_platform_api_key):
        # Deliberately terse: an unauthenticated caller learns nothing about
        # what a valid request looks like.
        log.warning("voice.webhook_rejected", extra=event(platform=PLATFORM_NAME))
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": {"code": "UNAUTHORIZED", "message": "Invalid credentials."}},
        )

    try:
        payload: Any = await request.json()
    except ValueError:
        log.warning("voice.webhook_unparseable", extra=event(platform=PLATFORM_NAME))
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=acknowledgement())

    if not isinstance(payload, dict):
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=acknowledgement())

    # Raises ServiceUnavailableError -> 503 through the standard handler.
    services = get_services(request)

    voice_event = parse_webhook(payload)
    log.info(
        "voice.webhook",
        extra=event(
            platform=PLATFORM_NAME,
            type=voice_event.raw_type,
            event=voice_event.event_type,
            call_id=voice_event.call_id or None,
            tool_calls=len(voice_event.tool_calls),
        ),
    )

    try:
        response = await services.conversation.handle(voice_event)
    except Exception:
        # The caller is mid-sentence. Log it and answer politely rather than
        # returning a 500 that would drop the call.
        log.exception("voice.webhook_failed", extra=event(call_id=voice_event.call_id or None))
        return JSONResponse(status_code=status.HTTP_200_OK, content=acknowledgement())

    if response.event_type is VoiceEventType.TOOL_CALLS:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=format_tool_results(response.tool_results, transfer_to=response.transfer_to),
        )

    return JSONResponse(status_code=status.HTTP_200_OK, content=acknowledgement())


@router.get("/assistant-config", summary="Assistant configuration for this build")
async def assistant_config(settings: SettingsDep, services: ServicesDep) -> dict[str, Any]:
    """The prompt and tool schemas to configure the Vapi assistant with.

    Generated from the code that implements the tools, so a schema cannot drift
    from its handler. Disabled in production: it is a setup aid, and the prompt
    is not something to serve publicly.
    """
    return {
        "platform": PLATFORM_NAME,
        "model": {
            "provider": "anthropic",
            "model": "claude-sonnet-5",
            "temperature": 0.3,
            "messages": [{"role": "system", "content": services.system_prompt}],
            "tools": tool_schemas(services.tools.definitions),
        },
        "firstMessage": (
            "Thanks for calling Observe Insurance, I'm the claims assistant. How can I help today?"
        ),
        "serverUrl": "<your public base URL>/api/v1/voice/webhook",
        "serverUrlSecret": "<the value of VOICE_PLATFORM_API_KEY>",
        "notes": {
            "secret_header": SECRET_HEADER,
            "environment": settings.environment,
        },
    }
