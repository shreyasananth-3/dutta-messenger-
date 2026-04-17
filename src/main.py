"""DuttaMessenger FastAPI Application.

Main application entry point. Composes the app from the observability and
security primitives in `src/shared/`, registers module routers behind feature
flags, and owns the startup/shutdown lifecycle.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from src.config import settings
from src.shared.database import close_db
from src.shared.exceptions import AppException
from src.shared.middleware.correlation_id import CorrelationIdMiddleware
from src.shared.middleware.request_logger import RequestLoggerMiddleware
from src.shared.observability import init_observability
from src.shared.redis import close_redis, redis_healthcheck
from src.shared.security import limiter, limiter_exception_handler

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle: startup and shutdown."""
    logger.info("starting_application", environment=settings.ENVIRONMENT)
    try:
        redis_ok = await redis_healthcheck()
        logger.info("redis_status", connected=redis_ok)
        yield
    finally:
        logger.info("shutting_down_application")
        await close_db()
        await close_redis()
        logger.info("application_shutdown_complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="DuttaMessenger",
        description="Private institutional messaging platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ---- Observability (logging + tracing + metrics + Sentry) ------------
    init_observability(app)

    # ---- Middleware (order: innermost registered last) -------------------
    app.add_middleware(RequestLoggerMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- Rate limiter ----------------------------------------------------
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, limiter_exception_handler)

    # ---- Global exception handlers --------------------------------------
    @app.exception_handler(AppException)
    async def _app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
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
    async def _general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
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
                    "details": {"error": str(exc)} if settings.DEBUG else {},
                }
            },
        )

    # ---- Health probe (Kubernetes readiness / liveness) ------------------
    @app.get("/health", tags=["ops"], include_in_schema=False)
    async def _health_check() -> dict[str, str]:
        return {"status": "healthy"}

    # ---- Module routers (each behind a feature flag) ---------------------
    from src.modules.auth.router import router as auth_router

    app.include_router(auth_router, prefix=settings.API_V1_PREFIX)

    if settings.ENABLE_USERS:
        from src.modules.users.router import router as users_router

        app.include_router(users_router, prefix=settings.API_V1_PREFIX)

    if settings.ENABLE_ACL:
        from src.modules.acl.router import router as acl_router

        app.include_router(acl_router, prefix=settings.API_V1_PREFIX)

    if settings.ENABLE_GROUPS:
        from src.modules.groups.router import router as groups_router

        app.include_router(groups_router, prefix=settings.API_V1_PREFIX)

    if settings.ENABLE_CHAT:
        from src.modules.chat.router import router as chat_router

        app.include_router(chat_router, prefix=settings.API_V1_PREFIX)

    if settings.ENABLE_MEDIA:
        from src.modules.media.router import router as media_router

        app.include_router(media_router, prefix=settings.API_V1_PREFIX)

    if settings.ENABLE_NOTIFICATIONS:
        from src.modules.notifications.router import router as notifications_router

        app.include_router(notifications_router, prefix=settings.API_V1_PREFIX)

    logger.info(
        "fastapi_app_created",
        api_version=settings.API_V1_PREFIX,
        enabled_modules=[
            name
            for name, on in [
                ("users", settings.ENABLE_USERS),
                ("acl", settings.ENABLE_ACL),
                ("groups", settings.ENABLE_GROUPS),
                ("chat", settings.ENABLE_CHAT),
                ("media", settings.ENABLE_MEDIA),
                ("notifications", settings.ENABLE_NOTIFICATIONS),
            ]
            if on
        ],
    )
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",  # noqa: S104 - intentional bind for container networks
        port=8000,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL,
    )
