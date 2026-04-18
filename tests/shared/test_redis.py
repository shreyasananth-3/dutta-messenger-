"""Tests for the Redis client wrapper.

The wrapper functions catch broad exceptions and return safe defaults so a
flaky Redis cannot break a request. Tests cover both the success path
(against a live local Redis) and the swallowed-error path (with a stub
client whose methods raise).
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio

import src.shared.redis as redis_mod
from src.shared.redis import (
    close_redis,
    delete_cache,
    get_cache,
    get_redis,
    publish_event,
    redis_healthcheck,
    set_cache,
    subscribe_channel,
)


@pytest_asyncio.fixture
async def live_redis() -> Any:
    """Yield a live redis client; skip the test if redis isn't reachable."""
    if not await redis_healthcheck():
        pytest.skip("Redis not reachable on localhost:6379")
    client = await get_redis()
    yield client
    await close_redis()


@pytest.mark.integration
class TestLiveRedis:
    @pytest.mark.asyncio
    async def test_healthcheck_returns_true(self, live_redis: Any) -> None:
        assert await redis_healthcheck() is True

    @pytest.mark.asyncio
    async def test_set_get_delete_cache_roundtrip(self, live_redis: Any) -> None:
        key = "dutta:test:cache:1"
        assert await set_cache(key, "hello", expire=10) is True
        assert await get_cache(key) == "hello"
        assert await delete_cache(key) is True
        assert await get_cache(key) is None

    @pytest.mark.asyncio
    async def test_publish_event_returns_subscriber_count(self, live_redis: Any) -> None:
        # No subscribers, so count should be 0.
        assert await publish_event("dutta:test:channel", "ping") == 0

    @pytest.mark.asyncio
    async def test_subscribe_channel_returns_pubsub(self, live_redis: Any) -> None:
        pubsub = await subscribe_channel("dutta:test:channel:sub")
        try:
            assert pubsub is not None
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()


class TestSwallowedErrors:
    """When the underlying client raises, helpers must return safe defaults."""

    @pytest.mark.asyncio
    async def test_set_cache_returns_false_on_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Bad:
            async def setex(self, *_a: Any, **_k: Any) -> None:
                raise RuntimeError("nope")

        async def _fake_get_redis() -> _Bad:
            return _Bad()

        monkeypatch.setattr(redis_mod, "get_redis", _fake_get_redis)
        assert await set_cache("k", "v") is False

    @pytest.mark.asyncio
    async def test_get_cache_returns_none_on_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Bad:
            async def get(self, *_a: Any, **_k: Any) -> None:
                raise RuntimeError("nope")

        async def _fake_get_redis() -> _Bad:
            return _Bad()

        monkeypatch.setattr(redis_mod, "get_redis", _fake_get_redis)
        assert await get_cache("k") is None

    @pytest.mark.asyncio
    async def test_delete_cache_returns_false_on_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Bad:
            async def delete(self, *_a: Any, **_k: Any) -> None:
                raise RuntimeError("nope")

        async def _fake_get_redis() -> _Bad:
            return _Bad()

        monkeypatch.setattr(redis_mod, "get_redis", _fake_get_redis)
        assert await delete_cache("k") is False

    @pytest.mark.asyncio
    async def test_healthcheck_returns_false_on_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Bad:
            async def ping(self) -> None:
                raise RuntimeError("down")

        async def _fake_get_redis() -> _Bad:
            return _Bad()

        monkeypatch.setattr(redis_mod, "get_redis", _fake_get_redis)
        assert await redis_healthcheck() is False
