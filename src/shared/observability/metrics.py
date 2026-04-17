"""Prometheus metrics for DuttaMessenger.

Exposes `/metrics` with:
- HTTP request counter + latency histogram (per method, path, status) — via
  prometheus-fastapi-instrumentator
- Application-specific gauges and counters (WebSocket connections, messages
  sent, notification fanout size, etc.) — declared here so every module
  imports from one place.

Metric names follow Prometheus conventions:
  <app>_<subsystem>_<unit>[_<suffix>]
"""

from __future__ import annotations

import structlog
from prometheus_client import Counter, Gauge, Histogram

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Application-level metrics (increment these from service code)
# ---------------------------------------------------------------------------

MESSAGES_SENT = Counter(
    "dutta_messages_sent_total",
    "Total messages sent, labelled by conversation type.",
    ["conversation_type"],
)

MESSAGE_DELIVERY_LATENCY = Histogram(
    "dutta_message_delivery_latency_seconds",
    "Latency from message persisted to all online recipients delivered.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)

WEBSOCKET_CONNECTIONS = Gauge(
    "dutta_websocket_connections",
    "Current number of active WebSocket connections.",
)

AUTH_FAILURES = Counter(
    "dutta_auth_failures_total",
    "Authentication failures, labelled by reason.",
    ["reason"],
)

RATE_LIMITED_REQUESTS = Counter(
    "dutta_rate_limited_requests_total",
    "Requests rejected by the rate limiter, labelled by rule.",
    ["rule"],
)

CELERY_TASK_LATENCY = Histogram(
    "dutta_celery_task_latency_seconds",
    "Celery task execution latency.",
    ["task_name"],
    buckets=(0.1, 0.5, 1.0, 5.0, 15.0, 60.0),
)


def register_metrics(app) -> None:  # type: ignore[no-untyped-def]
    """Attach prometheus-fastapi-instrumentator and expose `/metrics`."""
    try:
        from prometheus_fastapi_instrumentator import Instrumentator
    except ImportError:
        logger.warning("prometheus_instrumentator_missing_skipping")
        return

    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics", "/health"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

    logger.info("prometheus_metrics_registered", endpoint="/metrics")
