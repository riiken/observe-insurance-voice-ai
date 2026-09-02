"""Driving whole calls through the real stack.

A `Caller` speaks to the service the way Vapi does: real HTTP, real webhook
authentication, real payload parsing, real tool dispatch, real services. Only
the two Google Sheets are faked, at the repository boundary.

The point is that these read like transcripts. A scenario test should be
checkable against CLAUDE.md by someone who has not read the code.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.integrations.repositories import PersistResult
from app.integrations.voice_platform import SECRET_HEADER
from app.models.interaction import InteractionRecord
from app.models.session import SessionState
from app.services.container import ServiceContainer
from tests import voice_fixtures as vapi

WEBHOOK = "/api/v1/voice/webhook"
SECRET = "e2e-secret"

# Claim details from the fixture data. Nothing an unverified caller may hear.
CLAIM_SECRETS = (
    "CLM-88401",
    "CLM-88402",
    "under review",
    "documents required",
    "police report",
    "repair estimate",
)


class RecordingInteractions:
    """Integration #2, in memory, so a scenario can assert what was filed."""

    def __init__(self) -> None:
        self.saved: list[InteractionRecord] = []

    async def save(self, record: InteractionRecord) -> PersistResult:
        if any(existing.call_id == record.call_id for existing in self.saved):
            return PersistResult.already_recorded()
        self.saved.append(record)
        return PersistResult.persisted()

    def for_call(self, call_id: str) -> InteractionRecord | None:
        return next((r for r in self.saved if r.call_id == call_id), None)


class Caller:
    """One caller, one call, spoken to over the real webhook."""

    def __init__(
        self,
        client: TestClient,
        services: ServiceContainer,
        interactions: RecordingInteractions,
        call_id: str = "e2e-call",
    ) -> None:
        self._client = client
        self._services = services
        self._interactions = interactions
        self.call_id = call_id
        self.transcript: list[str] = []

    # --- what the platform does -------------------------------------------

    def dials(self, phone: str | None = None) -> None:
        self._post(vapi.call_started(call_id=self.call_id, phone=phone))

    def hangs_up(self) -> None:
        self._post(vapi.end_of_call(call_id=self.call_id))

    # --- what the agent does ----------------------------------------------

    def says(self, tool: str, **arguments: Any) -> str:
        """The agent calls a tool; returns the line spoken back."""
        response = self._post(vapi.tool_call(tool, arguments, call_id=self.call_id))
        body = response.json()
        spoken = body["results"][0]["result"] if body.get("results") else ""
        self.transcript.append(spoken)
        return spoken

    def gives_phone(self, number: str) -> str:
        return self.says("lookup_customer", phone_number=number)

    def gives_verification(self, value: str) -> str:
        return self.says("verify_identity", verification_value=value)

    def asks_about_claim(self) -> str:
        return self.says("get_claim_status")

    def asks(self, question: str) -> str:
        return self.says("search_faq", question=question)

    def asks_for_a_person(self, reason: str = "CALLER_REQUEST", **kwargs: Any) -> str:
        return self.says("request_representative", reason=reason, **kwargs)

    # --- what we can inspect afterwards ------------------------------------

    @property
    def services(self) -> ServiceContainer:
        return self._services

    @property
    def session(self) -> SessionState | None:
        return _await(self._services.sessions.get(self.call_id))

    @property
    def escalations(self) -> list:
        return self._services.escalation.records_for(self.call_id)

    @property
    def record(self) -> InteractionRecord | None:
        return self._interactions.for_call(self.call_id)

    @property
    def everything_said(self) -> str:
        return " ".join(self.transcript)

    def heard_claim_details(self) -> bool:
        spoken = self.everything_said.lower()
        return any(secret.lower() in spoken for secret in CLAIM_SECRETS)

    def _post(self, payload: dict):
        response = self._client.post(WEBHOOK, json=payload, headers={SECRET_HEADER: SECRET})
        assert response.status_code == 200, response.text
        return response


def _await(coroutine):
    """Run a coroutine from sync test code."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    return loop.run_until_complete(coroutine)
