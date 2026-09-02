"""Versioned API router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.voice import router as voice_router

api_v1_router = APIRouter()
api_v1_router.include_router(voice_router)
