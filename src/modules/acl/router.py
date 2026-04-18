"""Aggregated APIRouter for the ACL module."""

from __future__ import annotations

from fastapi import APIRouter

from src.modules.acl.routes.acl_routes import router as acl_router

router = APIRouter()
router.include_router(acl_router)

__all__ = ["router"]
