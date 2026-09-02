"""Shared FastAPI dependencies.

One place for everything injected into routes, so a route declares what it needs
instead of reaching for a module-level global. Phase 2 adds the session and
authenticated-caller dependencies here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.core.config import Settings, get_settings


def get_app_settings(request: Request) -> Settings:
    """Resolve the settings the running application was built with.

    `create_app` attaches its Settings to app state, so an explicitly configured
    app — tests, or an alternate entrypoint — is honoured. Falling back to the
    process-wide cache would silently ignore those settings.
    """
    settings: Settings | None = getattr(request.app.state, "settings", None)
    return settings if settings is not None else get_settings()


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
