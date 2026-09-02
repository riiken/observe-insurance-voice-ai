"""Detecting emergencies in what a caller says.

CLAUDE.md §14 requires that emergencies are handled as safety-sensitive
situations. Relying on the model alone to notice one would make the safety
behaviour a property of a prompt — something a confused or jailbroken model can
fail to do quietly. So there are **two independent detectors**: the agent, via
its instructions, and this module, which runs over the caller's own words on
every tool call. Either one triggers the emergency response.

## Why this is not a keyword list

An insurer's callers describe fires, crashes and injuries all day. "I'm calling
about the fire at my house last month" is a claim, not an emergency, and
telling that caller to hang up and dial 911 would be alarming, useless, and
would derail a legitimate call. A naive keyword match would do exactly that.

So detection is two-tier:

1. **Critical phrases** — unambiguous whatever the tense. Nobody says "he isn't
   breathing" about a claim from last March.
2. **Harm plus immediacy** — an ambiguous harm word ("fire", "trapped",
   "injured") counts only alongside a marker that it is happening *now*
   ("right now", "still", "help", "is on fire").

The bias is deliberate and worth stating plainly: a false positive costs a
caller one jarring sentence and an escalation to a human. A false negative
could cost considerably more. Where the two tiers disagree, this module
escalates — but it is built so the disagreement is rare, because a system that
cries emergency at every fire-damage claim would be turned off within a week.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.core.logging import event, get_logger

log = get_logger(__name__)


class SafetyLevel(StrEnum):
    NONE = "NONE"
    EMERGENCY = "EMERGENCY"


# --- tier 1: unambiguous, whatever the tense ---------------------------------
# Each of these describes a person in immediate danger. None of them is a way
# anyone describes a past insurance loss.
_CRITICAL_PATTERNS = [
    r"\b(?:is|isn't|is not|not|stopped)\s+breathing\b",
    r"\bcan'?t\s+breathe\b",
    r"\bunconscious\b",
    r"\bnot\s+responsive\b|\bunresponsive\b",
    r"\bheart\s+attack\b",
    r"\bstroke\b",
    r"\bseizure\b",
    r"\boverdos(?:e|ing)\b",
    r"\bcall\s+(?:an?\s+)?ambulance\b",
    r"\bcall\s+911\b|\bcalling\s+911\b|\bdial\s+911\b",
    r"\b(?:someone|somebody|he|she|they)\s+(?:is|are)\s+dying\b",
    r"\bbleeding\s+(?:badly|heavily|out)\b",
    r"\bgas\s+leak\b",
    r"\bcarbon\s+monoxide\b",
    r"\bkill\s+myself\b|\bsuicid(?:e|al)\b|\bend\s+my\s+life\b",
    r"\bbeing\s+attacked\b|\bintruder\b|\bbreak(?:ing)?\s+in\s+(?:right\s+)?now\b",
    r"\bmedical\s+emergency\b",
    # "Trapped" is not how anyone describes a settled claim; a person being
    # trapped is an emergency whether or not they add "right now".
    r"\b(?:someone|somebody|anyone|he|she|they|i|we)\s*(?:'?s|'?re|is|are|am)?\s*"
    r"(?:still\s+)?(?:trapped|stuck\s+inside|pinned)\b",
    r"\bit'?s\s+an\s+emergency\b|\bthis\s+is\s+an\s+emergency\b",
]

# --- tier 2: ambiguous harm, needs an immediacy marker ------------------------
# Every one of these is also normal claim vocabulary.
_HARM_PATTERNS = [
    r"\bon\s+fire\b",
    r"\bfire\b",
    r"\bsmoke\b",
    r"\btrapped\b|\bstuck\s+inside\b",
    r"\binjur(?:ed|y|ies)\b",
    r"\bhurt\b",
    r"\bbleeding\b",
    r"\bcrash(?:ed)?\b|\bcollision\b|\baccident\b",
    r"\bflood(?:ing|ed)?\b",
    r"\bcollaps(?:ed|ing)\b",
    r"\bhospital\b|\bambulance\b|\bparamedic\b",
]

# Markers that the harm is happening now rather than being reported after.
_IMMEDIACY_PATTERNS = [
    r"\bright\s+now\b",
    r"\bat\s+the\s+moment\b",
    r"\bcurrently\b",
    r"\bstill\b",
    r"\bhappening\b",
    r"\bhelp\s+me\b|\bplease\s+help\b|\bneed\s+help\b|\bhelp!\b",
    r"\bemergency\b",
    r"\bi'?m\s+(?:trapped|stuck|bleeding|hurt|injured)\b",
    r"\bis\s+on\s+fire\b|\bare\s+on\s+fire\b",
    r"\b(?:someone|somebody|he|she|they)\s*(?:'?s|is|are)\s+(?:hurt|injured|bleeding)\b",
    r"\bthere'?s\s+a\s+fire\b",
    r"\bwe'?re\s+(?:trapped|stuck|inside)\b",
    r"\bcan'?t\s+get\s+out\b",
    r"\bhurry\b|\bquickly\b|\bimmediately\b",
]

_CRITICAL = [re.compile(pattern, re.IGNORECASE) for pattern in _CRITICAL_PATTERNS]
_HARM = [re.compile(pattern, re.IGNORECASE) for pattern in _HARM_PATTERNS]
_IMMEDIACY = [re.compile(pattern, re.IGNORECASE) for pattern in _IMMEDIACY_PATTERNS]


@dataclass(frozen=True, slots=True)
class SafetyAssessment:
    """What the detector concluded, and on what basis."""

    level: SafetyLevel
    # Which rules fired. Logged so a false positive can be traced to a pattern
    # and fixed, rather than argued about.
    signals: tuple[str, ...] = ()

    @property
    def is_emergency(self) -> bool:
        return self.level is SafetyLevel.EMERGENCY


_SAFE = SafetyAssessment(SafetyLevel.NONE)


class SafetyService:
    """Classifies caller utterances. Pure, deterministic, no model call."""

    def assess(self, *texts: str | None) -> SafetyAssessment:
        """Assess everything the caller said in one turn."""
        combined = " ".join(text for text in texts if text).strip()
        if not combined:
            return _SAFE

        critical = [pattern.pattern for pattern in _CRITICAL if pattern.search(combined)]
        if critical:
            return self._emergency(critical, tier="CRITICAL")

        harm = [pattern.pattern for pattern in _HARM if pattern.search(combined)]
        if not harm:
            return _SAFE

        immediacy = [pattern.pattern for pattern in _IMMEDIACY if pattern.search(combined)]
        if not immediacy:
            # A harm word on its own is claim vocabulary, not a 999 call.
            return _SAFE

        return self._emergency(harm + immediacy, tier="HARM_WITH_IMMEDIACY")

    @staticmethod
    def _emergency(signals: list[str], *, tier: str) -> SafetyAssessment:
        # The utterance itself is never logged: it is the caller's own words and
        # may contain anything. Only which rules matched.
        log.warning(
            "safety.emergency_detected",
            extra=event(tier=tier, signals=len(signals)),
        )
        return SafetyAssessment(SafetyLevel.EMERGENCY, signals=tuple(signals))
