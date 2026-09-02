"""The names of the events this service emits.

Constants rather than string literals, because a log query is only as good as
the consistency of the name it filters on — and a typo in a log line is silent.
Anything alerting or dashboards key on lives here.

Convention: `domain.action`, and `domain.action.started` / `.completed` for
operations worth timing. A `.completed` event always carries `success` and
`duration_ms`, so one query answers both "how often" and "how slow".
"""

from __future__ import annotations

from typing import Final

# --- call lifecycle -----------------------------------------------------------
CALL_STARTED: Final = "call.started"
CALL_COMPLETED: Final = "call.completed"

# --- customer lookup ----------------------------------------------------------
CUSTOMER_LOOKUP_STARTED: Final = "customer.lookup.started"
CUSTOMER_LOOKUP_COMPLETED: Final = "customer.lookup.completed"

# --- authentication -----------------------------------------------------------
AUTHENTICATION_SUCCESS: Final = "authentication.success"
AUTHENTICATION_FAILED: Final = "authentication.failed"

# --- claims and knowledge -----------------------------------------------------
CLAIM_LOOKUP: Final = "claim.lookup"
FAQ_LOOKUP: Final = "faq.lookup"

# --- escalation and safety ----------------------------------------------------
ESCALATION_REQUESTED: Final = "escalation.requested"
SAFETY_EMERGENCY_DETECTED: Final = "safety.emergency_detected"

# --- tools --------------------------------------------------------------------
TOOL_INVOKED: Final = "tool.invoked"
TOOL_COMPLETED: Final = "tool.completed"
TOOL_ERROR: Final = "tool.error"

# --- post-call ----------------------------------------------------------------
POSTCALL_PERSISTED: Final = "postcall.persisted"
POSTCALL_FAILED: Final = "postcall.failed"
POSTCALL_DUPLICATE: Final = "postcall.duplicate"

# Everything above, for the test that asserts the required set is emitted.
ALL_EVENTS: Final = (
    CALL_STARTED,
    CALL_COMPLETED,
    CUSTOMER_LOOKUP_STARTED,
    CUSTOMER_LOOKUP_COMPLETED,
    AUTHENTICATION_SUCCESS,
    AUTHENTICATION_FAILED,
    CLAIM_LOOKUP,
    FAQ_LOOKUP,
    ESCALATION_REQUESTED,
    SAFETY_EMERGENCY_DETECTED,
    TOOL_INVOKED,
    TOOL_COMPLETED,
    TOOL_ERROR,
    POSTCALL_PERSISTED,
    POSTCALL_FAILED,
    POSTCALL_DUPLICATE,
)
