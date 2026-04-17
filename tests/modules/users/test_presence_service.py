"""Unit tests for presence_service (Redis online tracking)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.users.services import presence_service


@pytest.fixture
def mock_redis(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch `get_redis` to return a mock Redis client.

    We mock Redis here so tests don't require a live Redis at this level —
    the integration tests hit live Redis through the live client fixture.
    """
    client = MagicMock()
    client.set = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=1)
    client.exists = AsyncMock(return_value=0)
    # pipeline() returns an object with .exists() (queues a command) and
    # .execute() (awaitable, returns list of results sized to the queue
    # or `_preload` if tests want specific values).
    pipeline = MagicMock()
    pipeline._preload = None
    pipeline._queued = 0

    def _exists_queue(*_a: object, **_kw: object) -> MagicMock:
        pipeline._queued += 1
        return pipeline

    async def _execute() -> list[int]:
        n = pipeline._queued
        pipeline._queued = 0
        if pipeline._preload is not None:
            preload = pipeline._preload
            pipeline._preload = None
            return list(preload)
        return [0] * n

    pipeline.exists = MagicMock(side_effect=_exists_queue)
    pipeline.execute = AsyncMock(side_effect=_execute)
    client.pipeline = MagicMock(return_value=pipeline)

    async def _get_redis() -> MagicMock:
        return client

    monkeypatch.setattr(presence_service, "get_redis", _get_redis)
    return client


class TestMarkOnline:
    @pytest.mark.asyncio
    async def test_sets_key_with_ttl(self, mock_redis: MagicMock) -> None:
        user_id = uuid.uuid4()
        await presence_service.mark_online(user_id)
        mock_redis.set.assert_awaited_once_with(
            f"user:online:{user_id}", "1", ex=60
        )


class TestMarkOffline:
    @pytest.mark.asyncio
    async def test_deletes_key(self, mock_redis: MagicMock) -> None:
        user_id = uuid.uuid4()
        await presence_service.mark_offline(user_id)
        mock_redis.delete.assert_awaited_once_with(f"user:online:{user_id}")


class TestIsOnline:
    @pytest.mark.asyncio
    async def test_returns_false_when_key_absent(self, mock_redis: MagicMock) -> None:
        mock_redis.exists.return_value = 0
        assert await presence_service.is_online(uuid.uuid4()) is False

    @pytest.mark.asyncio
    async def test_returns_true_when_key_present(self, mock_redis: MagicMock) -> None:
        mock_redis.exists.return_value = 1
        assert await presence_service.is_online(uuid.uuid4()) is True


class TestGetOnlineMap:
    @pytest.mark.asyncio
    async def test_empty_input_returns_empty_map(self, mock_redis: MagicMock) -> None:
        result = await presence_service.get_online_map([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_mixed_results(self, mock_redis: MagicMock) -> None:
        u1 = uuid.uuid4()
        u2 = uuid.uuid4()
        u3 = uuid.uuid4()
        mock_redis.pipeline.return_value._preload = [1, 0, 1]

        result = await presence_service.get_online_map([u1, u2, u3])
        assert result == {u1: True, u2: False, u3: True}

    @pytest.mark.asyncio
    async def test_queues_one_command_per_user(
        self, mock_redis: MagicMock
    ) -> None:
        u1 = uuid.uuid4()
        u2 = uuid.uuid4()
        mock_redis.pipeline.return_value.execute.return_value = [1, 1]

        await presence_service.get_online_map([u1, u2])
        pipeline = mock_redis.pipeline.return_value
        # .exists() queued once per user
        assert pipeline.exists.call_count == 2
        pipeline.exists.assert_any_call(f"user:online:{u1}")
        pipeline.exists.assert_any_call(f"user:online:{u2}")
