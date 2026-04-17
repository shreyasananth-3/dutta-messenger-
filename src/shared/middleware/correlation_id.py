"""Correlation-ID middleware.

Attaches a per-request UUID to incoming requests, echoes it on responses via
`X-Request-ID`, and binds it to structlog so every log line produced during
the request can be traced back to it. Honours an inbound `X-Request-ID` if
provided (for multi-service tracing with upstream gateways) and falls back
to a fresh UUID4.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from src.shared.observability.logging import bind_correlation_id

HEADER = "X-Request-ID"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Reads or creates a correlation ID, binds it for the request lifetime."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        correlation_id = request.headers.get(HEADER) or str(uuid.uuid4())
        bind_correlation_id(correlation_id)
        response = await call_next(request)
        response.headers[HEADER] = correlation_id
        return response
