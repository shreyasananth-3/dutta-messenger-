"""Observability primitives for DuttaMessenger.

Single entry point `init_observability(app)` configures structured logging,
OpenTelemetry tracing, Prometheus metrics, and Sentry error tracking. Every
module built on top of `shared/` inherits these for free.
"""

from __future__ import annotations

from src.shared.observability.logging import bind_correlation_id, configure_logging
from src.shared.observability.metrics import register_metrics
from src.shared.observability.sentry import init_sentry
from src.shared.observability.tracing import init_tracing


def init_observability(app) -> None:  # type: ignore[no-untyped-def]
    """Wire up every observability subsystem onto a FastAPI app.

    Call once during `create_app()` after the app instance exists. Order
    matters: logging first (so later init calls log through structlog), then
    Sentry (so it catches instrumentation errors), then tracing, then metrics.
    """
    configure_logging()
    init_sentry()
    init_tracing(app)
    register_metrics(app)


__all__ = ["bind_correlation_id", "init_observability"]
