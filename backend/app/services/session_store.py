"""Where session state lives.

Server-side and keyed by `call_id`. The session is never serialised into a tool
result or a model prompt, so there is no path by which caller-influenced text
could propose an authentication status — the store is the only writer, and the
authentication service is the only caller that writes.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from app.core.logging import event, get_logger
from app.models.session import SessionState

log = get_logger(__name__)


@runtime_checkable
class SessionStore(Protocol):
    """Session persistence. Swapping in Redis is an implementation change only."""

    async def get(self, call_id: str) -> SessionState | None: ...

    async def save(self, session: SessionState) -> None: ...

    async def discard(self, call_id: str) -> None: ...


class InMemorySessionStore:
    """Process-local sessions.

    Adequate because a call is handled by one process for its lifetime, and a
    dropped call loses nothing that matters. It does mean sessions do not
    survive a restart or spread across replicas — the honest limit of a
    single-process deployment, tracked in docs/DEFERRED.md rather than papered
    over with infrastructure this assignment does not need.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}
        # Transitions are read-modify-write; the lock keeps two turns of the
        # same call from interleaving and losing an attempt count.
        self._lock = asyncio.Lock()

    async def get(self, call_id: str) -> SessionState | None:
        async with self._lock:
            return self._sessions.get(call_id)

    async def save(self, session: SessionState) -> None:
        async with self._lock:
            self._sessions[session.call_id] = session

    async def discard(self, call_id: str) -> None:
        async with self._lock:
            if self._sessions.pop(call_id, None) is not None:
                log.info("session.discarded", extra=event(call_id=call_id))

    async def count(self) -> int:
        """Live session count, for tests and diagnostics."""
        async with self._lock:
            return len(self._sessions)
