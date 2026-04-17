"""Sentry error tracking.

Opt-in via `SENTRY_DSN`. When unset, `init_sentry()` is a no-op so local dev
and tests never accidentally ship errors to a production project.
"""

from __future__ import annotations

import os

import structlog

logger = structlog.get_logger()


def init_sentry() -> None:
    """Initialise sentry-sdk if a DSN is configured."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        logger.debug("sentry_disabled_no_dsn")
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        logger.warning("sentry_sdk_missing_skipping")
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("ENVIRONMENT", "development"),
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        profiles_sample_rate=float(os.environ.get("SENTRY_PROFILES_SAMPLE_RATE", "0.0")),
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
            SqlalchemyIntegration(),
        ],
        send_default_pii=False,
    )
    logger.info("sentry_initialized")
