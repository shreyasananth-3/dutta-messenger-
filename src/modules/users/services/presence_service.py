"""Online-presence tracking, backed by Redis.

Per reference-docs/modules/users/MODULE.md: online status changes too
frequently to persist in Postgres. Redis keeps a 60-second TTL key per
online user, refreshed on every WebSocket heartbeat (to be implemented in
Stage 4d's chat module). This service exposes the read path used by the
users HTTP endpoints and exports the write-path helpers Stage 4d will
call from the WebSocket layer.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

import structlog

from src.shared.redis import get_redis

logger = structlog.get_logger()

# Key layout matches MODULE.md. Kept module-private on purpose — external
# code calls the helpers, never the raw key.
_KEY_PREFIX = "user:online:"
_TTL_SECONDS = 60


def _key(user_id: uuid.UUID | str) -> str:
    return f"{_KEY_PREFIX}{user_id}"


async def mark_online(user_id: uuid.UUID | str) -> None:
    """Mark a user online; called on WebSocket connect and on each heartbeat.

    Idempotent: re-calling within the TTL simply extends the key.
    """
    redis = await get_redis()
    await redis.set(_key(user_id), "1", ex=_TTL_SECONDS)


async def mark_offline(user_id: uuid.UUID | str) -> None:
    """Mark a user offline; called on clean WebSocket disconnect."""
    redis = await get_redis()
    await redis.delete(_key(user_id))


async def is_online(user_id: uuid.UUID | str) -> bool:
    """Return whether a single user is currently online."""
    redis = await get_redis()
    return bool(await redis.exists(_key(user_id)))


async def get_online_map(
    user_ids: Iterable[uuid.UUID | str],
) -> dict[uuid.UUID, bool]:
    """Return `{user_id: bool}` for every user_id passed.

    Uses a Redis pipeline so the round-trip is a single RTT regardless of
    how many users are queried. Caller is expected to bound the list
    (the HTTP route caps at 200).
    """
    ids = list(user_ids)
    if not ids:
        return {}
    redis = await get_redis()
    pipeline = redis.pipeline()
    for uid in ids:
        pipeline.exists(_key(uid))
    results = await pipeline.execute()
    return {
        uuid.UUID(str(uid)) if not isinstance(uid, uuid.UUID) else uid: bool(r)
        for uid, r in zip(ids, results, strict=True)
    }
