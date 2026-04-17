"""DuttaMessenger FastAPI Application.

Main application entry point with middleware setup,
router registration, and lifecycle management.
"""

import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from fastapi.responses import JSONResponse

from src.config import settings
from src.shared.database import close_db, init_db
from src.shared.exceptions import AppException
from src.shared.middleware.request_logger import RequestLoggerMiddleware
from src.shared.redis import close_redis, get_redis, redis_healthcheck

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: startup and shutdown.

    Args:
        app: FastAPI application instance.

    Yields:
        When application is running.
    """
    # Startup
    logger.info("starting_application", environment=settings.ENVIRONMENT)

    try:
        # Initialize database
        await init_db()
        logger.info("database_initialized")

        # Test Redis connection
        redis_ok = await redis_healthcheck()
        if redis_ok:
            logger.info("redis_connected")
        else:
            logger.warning("redis_connection_failed")

        yield  # Application runs here

    finally:
        # Shutdown
        logger.info("shutting_down_application")
        await close_db()
        await close_redis()
        logger.info("application_shutdown_complete")


def create_app() -> FastAPI:
    """Create and configure FastAPI application.

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(
        title="DuttaMessenger",
        description="Private institutional messaging platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS Configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request logging middleware
    app.add_middleware(RequestLoggerMiddleware)

    # Exception handlers
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        """Handle application-specific exceptions."""
        logger.error(
            "app_exception",
            error_code=exc.error_code,
            message=exc.message,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.error_code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Handle unexpected exceptions."""
        logger.error(
            "unhandled_exception",
            error=str(exc),
            path=request.url.path,
            exc_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected error occurred",
                    "details": {} if not settings.DEBUG else {"error": str(exc)},
                }
            },
        )

    # Health check endpoint
    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint.

        Returns:
            Health status.
        """
        return {"status": "healthy"}

    # API v1 routes
    from src.modules.auth.router import router as auth_router

    app.include_router(auth_router, prefix=settings.API_V1_PREFIX)

    # TODO: Include routers for other modules once they're created
    # app.include_router(users_router, prefix=settings.API_V1_PREFIX)
    # app.include_router(groups_router, prefix=settings.API_V1_PREFIX)
    # app.include_router(chat_router, prefix=settings.API_V1_PREFIX)
    # app.include_router(acl_router, prefix=settings.API_V1_PREFIX)
    # app.include_router(media_router, prefix=settings.API_V1_PREFIX)
    # app.include_router(notifications_router, prefix=settings.API_V1_PREFIX)

    logger.info("fastapi_app_created", api_version=settings.API_V1_PREFIX)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL,
    )
