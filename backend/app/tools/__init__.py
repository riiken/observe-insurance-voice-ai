"""Narrow, purpose-specific tools the agent may invoke.

Each tool validates its input, enforces its own authorization boundary, calls
one service, and returns a structured result — including for failures, which are
returned rather than raised so a broken upstream ends a sentence, not a call.
"""

from app.tools.base import ToolOutcome, ToolResult
from app.tools.claim_status import ClaimStatusTool

__all__ = ["ClaimStatusTool", "ToolOutcome", "ToolResult"]
