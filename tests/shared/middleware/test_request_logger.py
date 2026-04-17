"""Tests for the per-request structured logger middleware."""

from __future__ import annotations

import pytest
import structlog
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from src.shared.middleware.request_logger import RequestLoggerMiddleware


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestLoggerMiddleware)

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("simulated failure")

    return app


@pytest.mark.asyncio
async def test_success_logs_request_and_response() -> None:
    with capture_logs() as logs:
        async with AsyncClient(
            transport=ASGITransport(app=_build_app()), base_url="http://t"
        ) as c:
            r = await c.get("/ok?x=1")
            assert r.status_code == 200
    events = [entry["event"] for entry in logs]
    assert "http_request" in events
    assert "http_response" in events
    response_entry = next(e for e in logs if e["event"] == "http_response")
    assert response_entry["status_code"] == 200
    assert response_entry["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_error_path_logs_http_error_event() -> None:
    with capture_logs() as logs:
        async with AsyncClient(
            transport=ASGITransport(app=_build_app()), base_url="http://t"
        ) as c:
            with pytest.raises(RuntimeError):
                await c.get("/boom")
    events = [entry["event"] for entry in logs]
    assert "http_error" in events
