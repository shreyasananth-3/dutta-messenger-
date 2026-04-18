"""Aggregated APIRouter for the notifications module."""

from __future__ import annotations

from fastapi import APIRouter

from src.modules.notifications.routes.feed_routes import router as feed_router
from src.modules.notifications.routes.token_routes import router as token_router

router = APIRouter()
router.include_router(token_router)
router.include_router(feed_router)

__all__ = ["router"]
