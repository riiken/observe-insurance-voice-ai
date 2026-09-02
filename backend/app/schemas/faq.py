"""Structured FAQ results handed to the conversation layer."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.services.faq import Confidence


class FaqAnswerView(BaseModel):
    """One retrieved answer, with how well it actually matched.

    `confidence` is here so the agent can hedge or hand off rather than reading
    a marginal match with full conviction, and `source` so a surprising answer
    can be traced to the file that produced it.
    """

    topic: str
    answer: str = Field(description="Read aloud verbatim. Never paraphrased or extended.")
    confidence: Confidence
    relevance_score: float = Field(ge=0.0, le=1.0)
    source: str = Field(description="The knowledge file this came from.")
    matched_terms: list[str] = Field(default_factory=list)
    is_demo_content: bool = Field(
        default=True,
        description=(
            "Sample data for a take-home exercise, not a real insurer's policy. "
            "Every shipped knowledge file says so in the file itself."
        ),
    )
