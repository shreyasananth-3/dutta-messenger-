"""Auth module router.

Exports the auth routes for inclusion in the main FastAPI app.
"""

from fastapi import APIRouter

from src.modules.auth.routes.auth_routes import router as auth_router

router = APIRouter()
router.include_router(auth_router)

__all__ = ["router"]
