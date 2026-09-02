"""Session persistence."""

from __future__ import annotations

import asyncio

from app.models.session import SessionState
from app.services.session_store import InMemorySessionStore, SessionStore
from tests.session_fixtures import MARIA


async def test_a_saved_session_is_returned() -> None:
    store = InMemorySessionStore()
    await store.save(SessionState(call_id="call-1").with_customer_found(MARIA))

    session = await store.get("call-1")

    assert session is not None
    assert session.customer_id == "CUST-1001"


async def test_an_unknown_call_id_returns_none() -> None:
    """None must be handled as unauthenticated, never as a missing check."""
    assert await InMemorySessionStore().get("nope") is None


async def test_saving_replaces_the_previous_state() -> None:
    store = InMemorySessionStore()
    session = SessionState(call_id="call-1")
    await store.save(session)

    await store.save(session.with_authenticated(MARIA))

    stored = await store.get("call-1")
    assert stored is not None
    assert stored.is_authenticated is True


async def test_sessions_are_isolated_by_call_id() -> None:
    """One caller's authentication must never reach another's session."""
    store = InMemorySessionStore()
    await store.save(SessionState(call_id="call-1").with_authenticated(MARIA))
    await store.save(SessionState(call_id="call-2"))

    other = await store.get("call-2")

    assert other is not None
    assert other.is_authenticated is False


async def test_discard_removes_the_session() -> None:
    store = InMemorySessionStore()
    await store.save(SessionState(call_id="call-1"))

    await store.discard("call-1")

    assert await store.get("call-1") is None
    assert await store.count() == 0


async def test_discarding_an_unknown_call_is_harmless() -> None:
    await InMemorySessionStore().discard("never-existed")


async def test_concurrent_saves_do_not_corrupt_the_store() -> None:
    store = InMemorySessionStore()

    await asyncio.gather(
        *(store.save(SessionState(call_id=f"call-{index}")) for index in range(50))
    )

    assert await store.count() == 50


def test_the_in_memory_store_satisfies_the_protocol() -> None:
    assert isinstance(InMemorySessionStore(), SessionStore)
