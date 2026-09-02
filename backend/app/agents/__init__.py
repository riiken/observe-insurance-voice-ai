"""Conversation orchestration: the prompt and the platform-facing contract.

Agents decide what to *say*. They never implement a business rule — services do
that — and they cannot authorise anything: the prompt has no mechanism to mark a
caller verified, because no such mechanism exists.
"""

from app.agents.prompt import load_system_prompt
from app.agents.specialists import Intent, Specialist, Supervisor

__all__ = ["Intent", "Specialist", "Supervisor", "load_system_prompt"]
