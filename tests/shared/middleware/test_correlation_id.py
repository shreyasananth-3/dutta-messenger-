"""Tests for the X-Request-ID correlation middleware."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.shared.middleware.correlation_id import HEADER, CorrelationIdMiddleware


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "ok"}

    return app


@pytest.mark.asyncio
async def test_inbound_header_echoed() -> None:
    cid = "abc-123-incoming"
    async with AsyncClient(transport=ASGITransport(app=_build_app()), base_url="http://t") as c:
        r = await c.get("/ping", headers={HEADER: cid})
        assert r.status_code == 200
        assert r.headers[HEADER] == cid


@pytest.mark.asyncio
async def test_missing_header_generates_uuid() -> None:
    async with AsyncClient(transport=ASGITransport(app=_build_app()), base_url="http://t") as c:
        r = await c.get("/ping")
        cid = r.headers[HEADER]
        # Confirms it's a parseable UUID
        uuid.UUID(cid)


@pytest.mark.asyncio
async def test_each_request_gets_unique_id_when_missing() -> None:
    async with AsyncClient(transport=ASGITransport(app=_build_app()), base_url="http://t") as c:
        r1 = await c.get("/ping")
        r2 = await c.get("/ping")
        assert r1.headers[HEADER] != r2.headers[HEADER]
