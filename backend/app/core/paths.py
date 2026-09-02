"""Locating the content the service reads at startup.

Both the FAQ files and the claim guidance live in `knowledge/`, and where that
sits relative to the code depends on how the app was deployed:

    repository   <root>/backend/app/...   and   <root>/knowledge/
    container    /srv/app/...             and   /srv/knowledge/

Hard-coding one depth means the other silently fails to start. That is exactly
what happened: the Dockerfile set `CLAIM_GUIDANCE_PATH` to paper over it for one
file and not the other, so the image could not boot with the integration
configured — and it went unnoticed because the container smoke tests ran
unconfigured, where the content is never loaded.

So the directory is *found* rather than assumed, and an explicit setting still
wins for anyone who wants to point elsewhere.
"""

from __future__ import annotations

from pathlib import Path

# app/core/paths.py -> parents[1] is the `app` package.
_APP_PACKAGE = Path(__file__).resolve().parents[1]

# Ordered by how close the content sits to the code.
_CANDIDATES = (
    _APP_PACKAGE.parent / "knowledge",  # /srv/knowledge  (container)
    _APP_PACKAGE.parent.parent / "knowledge",  # <root>/knowledge (repository)
)


def knowledge_directory() -> Path:
    """The directory holding the FAQ files and claim guidance.

    Returns the first candidate that exists. If none does, returns the most
    likely one so the caller's error names a real path rather than a guess —
    startup fails either way, and a wrong path in the message wastes the time
    of whoever reads it.
    """
    for candidate in _CANDIDATES:
        if candidate.is_dir():
            return candidate
    return _CANDIDATES[-1]
