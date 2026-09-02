"""Operational metrics.

In-process counters and latency summaries, exposed as JSON at `/metrics`.

**Deliberately not Prometheus, StatsD or OpenTelemetry.** Those are the right
answer once there is somewhere to send the data; here they would add a
dependency and a sidecar to a service that has neither. This keeps the same
shape — counters and histograms keyed by labels — so swapping the collector for
a real client later touches this file and nothing else.

**Metrics are process-local and lossy by design.** They reset on restart and do
not aggregate across replicas. That is acceptable for aggregates in a way it
would not be for session state: losing a counter costs a gap in a graph, while
losing a session would drop a caller mid-verification. The distinction is the
whole reason one is global and the other is keyed and isolated.

Nothing here holds per-call data — only counts and durations — so it cannot
leak PII and does not grow with call volume.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LatencySummary:
    """Count, total, min and max. No percentiles.

    A p95 needs retained samples, which means an unbounded buffer or a sketch;
    neither is worth it when the numbers are read by a person during a demo.
    Mean and max answer "is it slow" well enough at this scale.
    """

    count: int = 0
    total_ms: float = 0.0
    min_ms: float | None = None
    max_ms: float | None = None

    def observe(self, duration_ms: float) -> None:
        self.count += 1
        self.total_ms += duration_ms
        self.min_ms = duration_ms if self.min_ms is None else min(self.min_ms, duration_ms)
        self.max_ms = duration_ms if self.max_ms is None else max(self.max_ms, duration_ms)

    @property
    def mean_ms(self) -> float:
        return round(self.total_ms / self.count, 2) if self.count else 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "mean_ms": self.mean_ms,
            "min_ms": round(self.min_ms, 2) if self.min_ms is not None else None,
            "max_ms": round(self.max_ms, 2) if self.max_ms is not None else None,
        }


@dataclass
class MetricsRegistry:
    """Counters and latencies for one process."""

    _counters: dict[str, int] = field(default_factory=dict)
    _latencies: dict[str, LatencySummary] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # --- recording --------------------------------------------------------

    def increment(self, name: str, amount: int = 1, **labels: str) -> None:
        key = _key(name, labels)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + amount

    def observe(self, name: str, duration_ms: float, **labels: str) -> None:
        key = _key(name, labels)
        with self._lock:
            self._latencies.setdefault(key, LatencySummary()).observe(duration_ms)

    # --- reading ----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Everything, plus the rates a person actually asks about."""
        with self._lock:
            counters = dict(self._counters)
            latencies = {key: summary.snapshot() for key, summary in self._latencies.items()}

        return {
            "counters": counters,
            "latencies": latencies,
            "rates": _derived_rates(counters),
        }

    def reset(self) -> None:
        """Test hook. Never called in a running service."""
        with self._lock:
            self._counters.clear()
            self._latencies.clear()


def _key(name: str, labels: dict[str, str]) -> str:
    if not labels:
        return name
    rendered = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
    return f"{name}{{{rendered}}}"


# --- metric names -------------------------------------------------------------

CALLS_TOTAL = "calls_total"
CALLS_COMPLETED = "calls_completed_total"
CALL_DURATION = "call_duration_ms"
TOOL_CALLS = "tool_calls_total"
TOOL_LATENCY = "tool_latency_ms"
AUTHENTICATION_ATTEMPTS = "authentication_attempts_total"
CUSTOMER_LOOKUPS = "customer_lookups_total"
CUSTOMER_LOOKUP_LATENCY = "customer_lookup_latency_ms"
CLAIM_LOOKUPS = "claim_lookups_total"
FAQ_LOOKUPS = "faq_lookups_total"
ESCALATIONS = "escalations_total"
POSTCALL = "postcall_total"
POSTCALL_LATENCY = "postcall_latency_ms"


def _ratio(numerator: int, denominator: int) -> float | None:
    """None rather than 0.0 when nothing has happened yet.

    A success rate of zero and a success rate of "no calls yet" mean very
    different things on a dashboard.
    """
    return round(numerator / denominator, 4) if denominator else None


def _sum_where(counters: dict[str, int], metric: str, label: str | None = None) -> int:
    """Total a metric across every label set, optionally filtered by one label.

    Summing rather than looking up an exact key matters: `tool_calls_total`
    carries a `tool` label as well as `outcome`, so the rendered key is
    `tool_calls_total{outcome=SUCCESS,tool=search_faq}` and an exact-match
    lookup silently returns zero. That produced a tool success rate of 0.0
    while every call was succeeding.
    """
    prefix = f"{metric}{{"
    return sum(
        value
        for key, value in counters.items()
        if key.startswith(prefix) and (label is None or label in _labels(key))
    )


def _labels(key: str) -> set[str]:
    inner = key[key.index("{") + 1 : -1] if "{" in key else ""
    return set(inner.split(",")) if inner else set()


def _derived_rates(counters: dict[str, int]) -> dict[str, float | None]:
    """The handful of rates worth reading directly."""
    auth_success = _sum_where(counters, AUTHENTICATION_ATTEMPTS, "outcome=success")
    auth_total = _sum_where(counters, AUTHENTICATION_ATTEMPTS)

    tool_success = _sum_where(counters, TOOL_CALLS, "outcome=SUCCESS")
    tool_total = _sum_where(counters, TOOL_CALLS)

    postcall_ok = _sum_where(counters, POSTCALL, "outcome=persisted")
    postcall_total = _sum_where(counters, POSTCALL)

    calls_total = counters.get(CALLS_TOTAL, 0)

    return {
        "authentication_success_rate": _ratio(auth_success, auth_total),
        "tool_success_rate": _ratio(tool_success, tool_total),
        "postcall_persistence_success_rate": _ratio(postcall_ok, postcall_total),
        "escalation_rate": _ratio(_sum_where(counters, ESCALATIONS), calls_total),
    }


# One registry per process. Global because metrics are aggregates: unlike a
# session, there is nothing here belonging to a particular call.
METRICS = MetricsRegistry()
