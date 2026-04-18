"""Unit tests for rate limit key derivation and exception handler."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from slowapi.errors import RateLimitExceeded

from src.shared.security.rate_limit import (
    _key,
    limiter,
    limiter_exception_handler,
)


class TestKeyFunction:
    def test_user_id_preferred_when_authenticated(self) -> None:
        request = SimpleNamespace(
            state=SimpleNamespace(user=SimpleNamespace(id="user-42")),
            client=SimpleNamespace(host="1.2.3.4"),
        )
        assert _key(request) == "user:user-42"

    def test_falls_back_to_client_host(self) -> None:
        request = SimpleNamespace(
            state=SimpleNamespace(user=None),
            client=SimpleNamespace(host="9.9.9.9"),
        )
        assert _key(request) == "ip:9.9.9.9"

    def test_no_client_returns_unknown(self) -> None:
        request = SimpleNamespace(
            state=SimpleNamespace(user=None),
            client=None,
        )
        assert _key(request) == "ip:unknown"

    def test_user_without_id_falls_back_to_ip(self) -> None:
        request = SimpleNamespace(
            state=SimpleNamespace(user=SimpleNamespace(id=None)),
            client=SimpleNamespace(host="5.5.5.5"),
        )
        assert _key(request) == "ip:5.5.5.5"


class TestLimiterInstance:
    def test_default_limit_configured(self) -> None:
        # slowapi wraps each default rule in a LimitGroup with the original
        # rule string stashed in a dunder-mangled attribute.
        providers = [
            getattr(rule, "_LimitGroup__limit_provider", "") for rule in limiter._default_limits
        ]
        assert any("300" in str(p) for p in providers)


class TestLimiterExceptionHandler:
    @pytest.mark.asyncio
    async def test_returns_429_envelope(self) -> None:
        # RateLimitExceeded.detail is set from the rule that fired.
        exc = RateLimitExceeded(SimpleNamespace(error_message="5/minute"))
        request = SimpleNamespace(state=SimpleNamespace(user=None), client=None)
        response = await limiter_exception_handler(request, exc)
        assert response.status_code == 429
        body = json.loads(response.body)
        assert body["error"]["code"] == "RATE_LIMITED"
        assert response.headers["Retry-After"] == "60"
