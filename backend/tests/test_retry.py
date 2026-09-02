"""Bounded retry: transient failures only, and a finite budget."""

from __future__ import annotations

import pytest

from app.core.retry import retry_async


class Transient(Exception):
    pass


class Permanent(Exception):
    pass


def _always_transient(exc: BaseException) -> bool:
    return isinstance(exc, Transient)


async def _no_sleep(_delay: float) -> None:
    return None


async def test_returns_the_first_successful_result() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result = await retry_async(
        operation,
        max_retries=3,
        backoff_base_seconds=0.01,
        is_transient=_always_transient,
        operation_name="test",
        sleep=_no_sleep,
    )

    assert result == "ok"
    assert calls == 1


async def test_retries_a_transient_failure_then_succeeds() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise Transient
        return "ok"

    result = await retry_async(
        operation,
        max_retries=3,
        backoff_base_seconds=0.01,
        is_transient=_always_transient,
        operation_name="test",
        sleep=_no_sleep,
    )

    assert result == "ok"
    assert attempts == 3


async def test_a_permanent_failure_is_not_retried() -> None:
    """Retrying a 404 only spends a live caller's patience."""
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise Permanent

    with pytest.raises(Permanent):
        await retry_async(
            operation,
            max_retries=5,
            backoff_base_seconds=0.01,
            is_transient=_always_transient,
            operation_name="test",
            sleep=_no_sleep,
        )

    assert attempts == 1


async def test_the_retry_budget_is_bounded() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise Transient

    with pytest.raises(Transient):
        await retry_async(
            operation,
            max_retries=2,
            backoff_base_seconds=0.01,
            is_transient=_always_transient,
            operation_name="test",
            sleep=_no_sleep,
        )

    assert attempts == 3  # the initial attempt plus two retries


async def test_backoff_grows_and_stays_jittered() -> None:
    delays: list[float] = []

    async def record(delay: float) -> None:
        delays.append(delay)

    async def operation() -> str:
        raise Transient

    with pytest.raises(Transient):
        await retry_async(
            operation,
            max_retries=3,
            backoff_base_seconds=1.0,
            is_transient=_always_transient,
            operation_name="test",
            sleep=record,
        )

    assert len(delays) == 3
    # Full jitter: each delay is somewhere in [0, base * 2**attempt].
    assert all(0 <= delay <= 1.0 * 2**index for index, delay in enumerate(delays))


async def test_max_retries_zero_means_a_single_attempt() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise Transient

    with pytest.raises(Transient):
        await retry_async(
            operation,
            max_retries=0,
            backoff_base_seconds=0.01,
            is_transient=_always_transient,
            operation_name="test",
            sleep=_no_sleep,
        )

    assert attempts == 1
