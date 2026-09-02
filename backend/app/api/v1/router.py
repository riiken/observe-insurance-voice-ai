"""Versioned API router.

Phase 1 exposes no business endpoints. The voice-platform webhook and tool
endpoints are mounted here in Phase 2.
"""

from __future__ import annotations

from fastapi import APIRouter

api_v1_router = APIRouter()
