"""Users module router.

Collects all user-facing routes for inclusion under the app's `/api/v1`
prefix. Registered in `src/main.py` behind `settings.ENABLE_USERS`.
"""

from fastapi import APIRouter

from src.modules.users.routes.user_routes import router as user_routes_router

router = APIRouter()
router.include_router(user_routes_router)

__all__ = ["router"]
