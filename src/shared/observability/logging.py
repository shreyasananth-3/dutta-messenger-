"""Structured logging configuration.

Uses structlog with a correlation-ID context variable so every log line in a
request lifecycle can be traced back to the same request, whether it was
emitted from a route, a background task, or a WebSocket handler.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

import structlog

from src.config import settings

_correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def bind_correlation_id(correlation_id: str | None) -> None:
    """Bind a correlation ID to the current async context.

    Every subsequent structlog call in this async task will include the ID
    automatically. Used by the correlation-ID middleware and by Celery tasks
    that receive the ID from the message header.
    """
    _correlation_id_var.set(correlation_id)


def _add_correlation_id(
    _logger: object, _method_name: str, event_dict: dict[str, object]
) -> dict[str, object]:
    """structlog processor that injects the current correlation ID."""
    cid = _correlation_id_var.get()
    if cid is not None:
        event_dict.setdefault("correlation_id", cid)
    return event_dict


def configure_logging() -> None:
    """Configure structlog + stdlib logging for the whole process.

    JSON output in non-development environments (machine-parseable for Loki
    / ELK); human-friendly pretty rendering otherwise.
    """
    level = logging.getLevelName(settings.LOG_LEVEL.upper())
    if not isinstance(level, int):
        level = logging.INFO

    logging.basicConfig(
        level=level,
        format="%(message)s",
        stream=sys.stdout,
        force=True,
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_correlation_id,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.ENVIRONMENT == "development":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
