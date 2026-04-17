"""OpenTelemetry distributed tracing.

Auto-instruments FastAPI, SQLAlchemy, and Redis so every HTTP request emits a
trace that spans DB queries and cache lookups. Exporter is OTLP so traces can
be pointed at Jaeger, Tempo, Grafana Cloud, Honeycomb, or any OTLP-compatible
backend by setting `OTEL_EXPORTER_OTLP_ENDPOINT`.

Tracing is opt-in via `OTEL_ENABLED=true` — when off, all calls are no-ops and
there is zero runtime cost.
"""

from __future__ import annotations

import os

import structlog

logger = structlog.get_logger()


def _enabled() -> bool:
    """Tracing is active only when explicitly enabled via env var."""
    return os.environ.get("OTEL_ENABLED", "false").lower() in {"1", "true", "yes"}


def init_tracing(app) -> None:  # type: ignore[no-untyped-def]
    """Initialise OTel tracing and auto-instrument FastAPI + SQLAlchemy + Redis.

    Safe to call when OTel libs aren't installed or tracing is disabled —
    the function exits quietly instead of crashing app startup.
    """
    if not _enabled():
        logger.debug("otel_disabled")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("otel_libs_missing_skipping")
        return

    resource = Resource.create(
        {
            SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", "dutta-messenger"),
            "deployment.environment": os.environ.get("ENVIRONMENT", "development"),
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")
    SQLAlchemyInstrumentor().instrument()
    RedisInstrumentor().instrument()

    logger.info("otel_initialized", endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))
