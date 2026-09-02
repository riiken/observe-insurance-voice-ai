"""Bounded retry with exponential backoff and jitter.

Only *transient* failures are retried. Retrying a 404 or a malformed sheet wastes
a caller's time on the phone and cannot succeed, so `is_transient` decides and
the caller supplies it.

Jitter matters here: without it, every concurrent call that hits the same Sheets
rate limit retries in lockstep and re-creates the burst that caused it.

**Attempts are not the only budget that matters.** Three attempts at ten seconds
each is thirty seconds of silence on a phone call, by which point the caller has
hung up and the retry is pointless. `total_budget_seconds` caps the wall-clock
time the whole operation may take, so an in-call lookup fails fast and says
something rather than succeeding to nobody.
"""

from __future__ import annotations

import asyncio
import random
import time
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
    total_budget_seconds: float | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> T:
    """Run `operation`, retrying transient failures within both budgets.

    Returns the first successful result. Re-raises the last exception when
    either budget is exhausted, or immediately if the failure is not transient.

    `total_budget_seconds` bounds wall-clock time across all attempts. It is
    what keeps a live caller from waiting through a full retry schedule; None
    means attempts are the only limit, which suits work nobody is waiting on.

    `sleep` and `monotonic` are injectable so tests exercise the schedule
    without waiting.
    """
    started = monotonic()
    attempt = 0

    while True:
        try:
            return await operation()
        except Exception as exc:
            if not is_transient(exc) or attempt >= max_retries:
                raise

            delay = _backoff_delay(attempt, backoff_base_seconds)
            elapsed = monotonic() - started

            if total_budget_seconds is not None and (elapsed + delay >= total_budget_seconds):
                # Retrying would overrun the budget. Giving up now leaves time
                # to say something; retrying leaves the caller in silence.
                log.warning(
                    "retry.budget_exhausted",
                    extra=event(
                        operation=operation_name,
                        attempt=attempt,
                        elapsed_seconds=round(elapsed, 3),
                        budget_seconds=total_budget_seconds,
                    ),
                )
                raise

            attempt += 1
            log.warning(
                "retry.attempt",
                extra=event(
                    operation=operation_name,
                    attempt=attempt,
                    max_retries=max_retries,
                    delay_seconds=round(delay, 3),
                    elapsed_seconds=round(elapsed, 3),
                    error=type(exc).__name__,
                ),
            )
            await sleep(delay)


def _backoff_delay(attempt: int, base_seconds: float) -> float:
    """Exponential backoff with full jitter, capped."""
    ceiling = min(base_seconds * (2**attempt), _MAX_BACKOFF_SECONDS)
    return random.uniform(0, ceiling)
