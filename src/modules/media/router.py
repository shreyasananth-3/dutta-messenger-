"""Media module router.

Collects all media-related routes for inclusion under the app's `/api/v1`
prefix. Registered in `src/main.py` behind `settings.ENABLE_MEDIA`.
"""

from fastapi import APIRouter

from src.modules.media.routes.media_routes import router as media_routes_router

router = APIRouter()
router.include_router(media_routes_router)

__all__ = ["router"]
