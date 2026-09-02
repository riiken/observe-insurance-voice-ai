"""The agent's system prompt.

Kept in a Markdown file next to this module rather than in a Python string, so
it can be reviewed and edited as prose — which is what it is.

What is deliberately *not* in it: any business rule that matters. The prompt
tells the agent how to sound and which tool to reach for; it does not decide who
is authenticated, what a claim says, or what the office hours are. Those live in
services and configured content, so a prompt that gets rewritten — or argued
with — cannot change them (CLAUDE.md §5).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.core.errors import AppError
from app.core.logging import event, get_logger

log = get_logger(__name__)

DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "claims_agent.md"


class PromptConfigurationError(AppError):
    code = "PROMPT_CONFIGURATION_ERROR"
    message = "The agent prompt is not configured correctly."


@lru_cache(maxsize=4)
def load_system_prompt(path: Path | None = None) -> str:
    """Read the system prompt. Cached: the file does not change at runtime."""
    source = path or DEFAULT_PROMPT_PATH

    try:
        prompt = source.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PromptConfigurationError(
            "Agent prompt file could not be read.", path=str(source)
        ) from exc

    if not prompt:
        raise PromptConfigurationError("Agent prompt file is empty.", path=str(source))

    log.info("prompt.loaded", extra=event(path=str(source), characters=len(prompt)))
    return prompt
