"""Aggregated APIRouter for the groups module."""

from __future__ import annotations

from fastapi import APIRouter

from src.modules.groups.routes.group_routes import router as group_router

router = APIRouter()
router.include_router(group_router)

__all__ = ["router"]
