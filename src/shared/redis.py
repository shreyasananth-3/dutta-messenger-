"""Redis client configuration for DuttaMessenger.

Provides centralized Redis connection for caching, pub/sub,
and distributed locks.
"""

from typing import Any

from redis.asyncio import Redis

from src.config import settings

# Global Redis instance
_redis_client: Redis | None = None


async def get_redis() -> Redis:
    """Get or create Redis client.

    Returns:
        Redis async client.
    """
    global _redis_client
    if _redis_client is None:
        _redis_client = await Redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=settings.REDIS_POOL_SIZE,
        )
    return _redis_client


async def close_redis() -> None:
    """Close Redis connection.

    Should be called at application shutdown.
    """
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None


async def redis_healthcheck() -> bool:
    """Check Redis connection health.

    Returns:
        True if Redis is accessible, False otherwise.
    """
    try:
        redis = await get_redis()
        await redis.ping()
        return True
    except Exception:
        return False


async def set_cache(key: str, value: str, expire: int = 3600) -> bool:
    """Set value in Redis cache.

    Args:
        key: Cache key.
        value: Value to cache (JSON serialized string).
        expire: Expiration time in seconds.

    Returns:
        True if set successfully.
    """
    try:
        redis = await get_redis()
        await redis.setex(key, expire, value)
        return True
    except Exception:
        return False


async def get_cache(key: str) -> str | None:
    """Get value from Redis cache.

    Args:
        key: Cache key.

    Returns:
        Cached value or None if not found.
    """
    try:
        redis = await get_redis()
        return await redis.get(key)
    except Exception:
        return None


async def delete_cache(key: str) -> bool:
    """Delete value from Redis cache.

    Args:
        key: Cache key.

    Returns:
        True if deleted successfully.
    """
    try:
        redis = await get_redis()
        await redis.delete(key)
        return True
    except Exception:
        return False


async def publish_event(channel: str, message: str) -> int:
    """Publish message to Redis pub/sub channel.

    Args:
        channel: Channel name.
        message: Message to publish.

    Returns:
        Number of subscribers that received the message.
    """
    redis = await get_redis()
    return await redis.publish(channel, message)


async def subscribe_channel(channel: str) -> Any:
    """Subscribe to Redis pub/sub channel.

    Args:
        channel: Channel name.

    Returns:
        Redis PubSub object for listening to messages.
    """
    redis = await get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    return pubsub
