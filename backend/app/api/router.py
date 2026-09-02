"""Top-level router assembly.

Health probes are mounted at the root; product endpoints under the configured
API prefix.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from app.api.health import router as health_router
from app.api.v1.router import api_v1_router


def register_routes(app: FastAPI, api_prefix: str) -> None:
    root = APIRouter()
    root.include_router(health_router)

    app.include_router(root)
    app.include_router(api_v1_router, prefix=api_prefix)
