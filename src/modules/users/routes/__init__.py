"""HTTP routes for the users module."""

from src.modules.users.routes.user_routes import router as user_routes_router

__all__ = ["user_routes_router"]
