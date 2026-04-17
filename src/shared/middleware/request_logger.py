"""Request/response logging middleware for DuttaMessenger.

Logs all HTTP requests and responses with structured logging
for observability and debugging.
"""

import time
import uuid
from typing import Any

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger()


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    """Middleware to log all HTTP requests and responses."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        """Log request and response.

        Args:
            request: Incoming HTTP request.
            call_next: Next middleware/handler.

        Returns:
            HTTP response.
        """
        # Generate request ID
        request_id = str(uuid.uuid4())

        # Log request
        logger.info(
            "http_request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            query=dict(request.query_params),
            client_host=request.client.host if request.client else None,
        )

        # Track timing
        start_time = time.time()

        try:
            response = await call_next(request)
            duration = time.time() - start_time

            # Log response
            logger.info(
                "http_response",
                request_id=request_id,
                status_code=response.status_code,
                duration_ms=round(duration * 1000, 2),
            )

            return response
        except Exception as e:
            duration = time.time() - start_time

            logger.error(
                "http_error",
                request_id=request_id,
                error=str(e),
                duration_ms=round(duration * 1000, 2),
            )
            raise
