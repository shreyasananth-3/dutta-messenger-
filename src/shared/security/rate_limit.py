"""Rate limiting via slowapi.

Default key: the requesting user's ID when authenticated, else the client IP.
This punishes abusive users without penalising entire NATs behind one IP.
Per-endpoint overrides are applied with the `@limiter.limit("5/minute")`
decorator in route modules. Rejected requests emit a Prometheus counter.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.shared.observability.metrics import RATE_LIMITED_REQUESTS


def _key(request: Request) -> str:
    """Prefer authenticated user-id, fall back to client host."""
    user = getattr(request.state, "user", None)
    if user is not None and getattr(user, "id", None):
        return f"user:{user.id}"
    client = request.client
    return f"ip:{client.host}" if client else "ip:unknown"


limiter = Limiter(key_func=_key, headers_enabled=True, default_limits=["300/minute"])


async def limiter_exception_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Custom handler so the error envelope matches API_STANDARDS.md."""
    RATE_LIMITED_REQUESTS.labels(rule=str(exc.detail)).inc()
    return JSONResponse(
        status_code=429,
        content={
            "error": {
                "code": "RATE_LIMITED",
                "message": "Too many requests. Please slow down and retry.",
                "details": {"limit": str(exc.detail)},
            }
        },
        headers={"Retry-After": "60"},
    )
