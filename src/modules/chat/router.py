"""Aggregated APIRouter for the chat module."""

from __future__ import annotations

from fastapi import APIRouter

from src.modules.chat.routes.chat_routes import router as rest_router
from src.modules.chat.routes.ws_routes import router as ws_router

router = APIRouter()
router.include_router(rest_router)
router.include_router(ws_router)

__all__ = ["router"]
