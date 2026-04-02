"""
Redis Client Wrapper

Provides async Redis client for MCP session caching and distributed locking.
"""
import redis.asyncio as redis
from typing import Optional
import logging

from app.config import get_settings

logger = logging.getLogger("algoquest.redis")

_redis_client: Optional['RedisClient'] = None


class RedisClient:
    """Async Redis client wrapper."""

    def __init__(self, redis_url: str, password: Optional[str] = None):
        self.redis_url = redis_url
        self.password = password
        self._client: Optional[redis.Redis] = None

    async def _get_client(self) -> redis.Redis:
        """Get or create Redis client connection."""
        if self._client is None:
            self._client = await redis.from_url(
                self.redis_url,
                password=self.password or None,
                encoding="utf-8",
                decode_responses=True
            )
            logger.info("Redis client connected")
        return self._client

    async def get(self, key: str) -> Optional[str]:
        """Get value from Redis."""
        client = await self._get_client()
        return await client.get(key)

    async def set(self, key: str, value: str, nx: bool = False, ex: Optional[int] = None) -> bool:
        """Set value in Redis. nx=True: only set if key doesn't exist."""
        client = await self._get_client()
        result = await client.set(key, value, nx=nx, ex=ex)
        return result is not None and result is not False

    async def setex(self, key: str, seconds: int, value: str) -> bool:
        """Set a value that automatically expires after ``seconds``.

        Convenience wrapper around :meth:`set` with ``ex`` populated.
        Returns ``True`` on success, ``False`` if the underlying SET failed.
        """
        return await self.set(key, value, ex=seconds)

    async def delete(self, *keys: str) -> int:
        """Delete one or more keys. Returns number deleted."""
        client = await self._get_client()
        return await client.delete(*keys)

    async def ttl(self, key: str) -> int:
        """Get time-to-live for key in seconds. Returns -2 if key doesn't exist."""
        client = await self._get_client()
        return await client.ttl(key)

    async def ping(self) -> bool:
        """Ping Redis to check connection."""
        try:
            client = await self._get_client()
            return await client.ping()
        except Exception as e:
            logger.error(f"Redis ping failed: {e}")
            return False

    async def close(self):
        """Close Redis connection."""
        if self._client:
            await self._client.aclose()
            self._client = None


def get_redis_client() -> RedisClient:
    """Get or create singleton Redis client instance.

    Thread-safe in async context: synchronous initialization cannot be
    interrupted by other coroutines in the single-threaded event loop.
    """
    global _redis_client

    if _redis_client is None:
        settings = get_settings()
        _redis_client = RedisClient(
            redis_url=settings.redis_url,
            password=settings.redis_password or None
        )
        logger.info("Redis client instance created")

    return _redis_client
