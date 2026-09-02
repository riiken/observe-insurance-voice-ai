"""Bounded retry with exponential backoff and jitter.

Only *transient* failures are retried. Retrying a 404 or a malformed sheet wastes
a caller's time on the phone and cannot succeed, so `is_transient` decides and
the caller supplies it.

Jitter matters here: without it, every concurrent call that hits the same Sheets
rate limit retries in lockstep and re-creates the burst that caused it.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.logging import event, get_logger

T = TypeVar("T")

log = get_logger(__name__)

# Cap on a single sleep, so a large retry budget cannot strand a live call.
_MAX_BACKOFF_SECONDS = 5.0


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    backoff_base_seconds: float,
    is_transient: Callable[[BaseException], bool],
    operation_name: str,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run `operation`, retrying transient failures up to `max_retries` times.

    Returns the first successful result. Re-raises the last exception once the
    budget is exhausted, or immediately if the failure is not transient.

    `sleep` is injectable so tests exercise the backoff schedule without
    actually waiting.
    """
    attempt = 0
    while True:
        try:
            return await operation()
        except Exception as exc:
            if not is_transient(exc) or attempt >= max_retries:
                raise

            delay = _backoff_delay(attempt, backoff_base_seconds)
            attempt += 1
            log.warning(
                "retry.attempt",
                extra=event(
                    operation=operation_name,
                    attempt=attempt,
                    max_retries=max_retries,
                    delay_seconds=round(delay, 3),
                    error=type(exc).__name__,
                ),
            )
            await sleep(delay)


def _backoff_delay(attempt: int, base_seconds: float) -> float:
    """Exponential backoff with full jitter, capped."""
    ceiling = min(base_seconds * (2**attempt), _MAX_BACKOFF_SECONDS)
    return random.uniform(0, ceiling)
