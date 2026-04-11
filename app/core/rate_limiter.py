"""
Rate Limiting Middleware for Sentinel API
==========================================

Uses Redis-backed token bucket for distributed rate limiting across multiple instances.
Automatically falls back to in-memory storage if Redis is unavailable (development mode).

Limits:
  - General API:     60 requests/minute per IP
  - Auth endpoints:  10 requests/minute per IP (brute-force protection)
  - AI endpoints:    20 requests/minute per IP (LLM cost protection)
  - WebSocket:       5 connections/minute per IP
  - File upload:     10 requests/minute per IP

Architecture:
  - RedisTokenBucket: Distributed rate limiting using Redis hash keys
  - TokenBucket: In-memory fallback for development/testing
  - Automatic failover when Redis connection fails
"""

import time
import logging
from collections import defaultdict
from typing import Dict, Tuple, Optional
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("sentinel.ratelimit")

# ─── Token Bucket Implementation ──────────────────────────────────────────


class TokenBucket:
    """Simple in-memory token bucket rate limiter."""

    def __init__(self):
        # {(client_ip, bucket_name): (tokens, last_refill_time)}
        self._buckets: Dict[Tuple[str, str], Tuple[float, float]] = {}
        self._last_cleanup: float = time.time()

    def is_allowed(
        self,
        client_ip: str,
        bucket_name: str = "default",
        max_tokens: int = 60,
        refill_rate: float = 1.0,  # tokens per second
    ) -> Tuple[bool, dict]:
        """
        Check if request is allowed and consume a token.
        Returns (allowed, rate_limit_info).
        """
        # Periodically prune stale entries to prevent unbounded growth
        now_mono = time.time()
        if now_mono - self._last_cleanup > 60:
            self.cleanup()
            self._last_cleanup = now_mono

        key = (client_ip, bucket_name)
        now = time.time()

        if key in self._buckets:
            tokens, last_refill = self._buckets[key]
            # Refill tokens based on elapsed time
            elapsed = now - last_refill
            tokens = min(max_tokens, tokens + elapsed * refill_rate)
        else:
            tokens = float(max_tokens)
            last_refill = now

        info = {
            "X-RateLimit-Limit": str(max_tokens),
            "X-RateLimit-Remaining": str(max(0, int(tokens) - 1)),
            "X-RateLimit-Bucket": bucket_name,
        }

        if tokens < 1.0:
            # Calculate retry-after
            retry_after = (1.0 - tokens) / refill_rate
            info["Retry-After"] = str(int(retry_after) + 1)
            self._buckets[key] = (tokens, now)
            return False, info

        # Consume a token
        self._buckets[key] = (tokens - 1.0, now)
        return True, info

    def cleanup(self, max_age: float = 300.0):
        """Remove stale bucket entries (call periodically)."""
        now = time.time()
        stale_keys = [
            k for k, (_, last) in self._buckets.items() if now - last > max_age
        ]
        for k in stale_keys:
            del self._buckets[k]


_LUA_TOKEN_BUCKET = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local capacity = tonumber(ARGV[3])

local data = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(data[1]) or capacity
local last_refill = tonumber(data[2]) or now

local elapsed = now - last_refill
local refill = elapsed * rate
tokens = math.min(capacity, tokens + refill)

if tokens >= 1 then
    tokens = tokens - 1
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
    redis.call('EXPIRE', key, 3600)
    return 1
else
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
    redis.call('EXPIRE', key, 3600)
    return 0
end
"""


class RedisTokenBucket:
    """Redis-backed distributed rate limiter with in-memory fallback."""

    def __init__(self, redis_url: Optional[str] = None):
        self.redis = None
        self._lua_sha: Optional[str] = None
        if redis_url:
            try:
                import redis

                self.redis = redis.from_url(redis_url)
                # Verify connection
                self.redis.ping()
                # Pre-load Lua script for atomic token bucket operations
                self._lua_sha = self.redis.script_load(_LUA_TOKEN_BUCKET)
                logger.info("Redis rate limiter connected")
            except Exception as e:
                logger.warning("Redis unavailable, falling back to in-memory: %s", e)
                self.redis = None

        self._fallback = TokenBucket()

    def is_allowed(
        self,
        client_ip: str,
        bucket_name: str = "default",
        max_tokens: int = 60,
        refill_rate: float = 1.0,
    ) -> Tuple[bool, dict]:
        if self.redis:
            return self._redis_is_allowed(
                client_ip, bucket_name, max_tokens, refill_rate
            )
        return self._fallback.is_allowed(
            client_ip, bucket_name, max_tokens, refill_rate
        )

    def _redis_is_allowed(
        self,
        client_ip: str,
        bucket_name: str,
        max_tokens: int,
        refill_rate: float,
    ) -> Tuple[bool, dict]:
        key = f"ratelimit:{bucket_name}:{client_ip}"
        now = time.time()

        try:
            # Atomic token bucket via Lua script -- no TOCTOU race
            allowed = self.redis.evalsha(
                self._lua_sha, 1, key, now, refill_rate, max_tokens
            )

            info = {
                "X-RateLimit-Limit": str(max_tokens),
                "X-RateLimit-Bucket": bucket_name,
            }

            if allowed:
                info["X-RateLimit-Remaining"] = str(max(0, max_tokens - 1))
                return True, info
            else:
                info["X-RateLimit-Remaining"] = "0"
                retry_after = int(1.0 / refill_rate) + 1
                info["Retry-After"] = str(retry_after)
                return False, info

        except Exception as e:
            logger.warning("Redis rate limit error, falling back: %s", e)
            return self._fallback.is_allowed(
                client_ip, bucket_name, max_tokens, refill_rate
            )


# Global instance - initialized lazily to avoid circular imports
rate_limiter = None


def get_rate_limiter():
    """Get or create the rate limiter instance with Redis support."""
    global rate_limiter
    if rate_limiter is None:
        # Import settings here to avoid circular imports
        from app.config import get_settings
        settings = get_settings()

        # Use RedisTokenBucket with fallback to in-memory for development
        rate_limiter = RedisTokenBucket(redis_url=settings.redis_url)
        logger.info("Rate limiter initialized with Redis URL: %s", settings.redis_url)
    return rate_limiter


# ─── Route Classification ─────────────────────────────────────────────────


def classify_route(path: str) -> Tuple[str, int, float]:
    """
    Classify API route into rate limit bucket.
    Returns (bucket_name, max_tokens, refill_rate).
    """
    path_lower = path.lower()

    if "/auth" in path_lower or "/login" in path_lower or "/register" in path_lower:
        return "auth", 10, 0.17  # 10/min
    elif "/ai" in path_lower or "/ask" in path_lower:
        return "ai", 20, 0.33  # 20/min
    elif "/upload" in path_lower or "/ingestion" in path_lower:
        return "upload", 10, 0.17  # 10/min
    elif "/ws" in path_lower:
        return "websocket", 5, 0.08  # 5/min
    elif "/admin" in path_lower:
        return "admin", 30, 0.5  # 30/min
    else:
        return "general", 60, 1.0  # 60/min


# ─── Middleware ────────────────────────────────────────────────────────────


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that applies rate limiting per IP and route bucket."""

    async def dispatch(self, request: Request, call_next):
        # Skip health checks and OPTIONS preflight
        if (
            request.url.path in ("/", "/health", "/ready")
            or request.method == "OPTIONS"
        ):
            return await call_next(request)

        # Get client IP -- use the direct connection address to prevent
        # X-Forwarded-For spoofing by untrusted clients.
        client_ip = request.client.host if request.client else "unknown"

        bucket_name, max_tokens, refill_rate = classify_route(request.url.path)

        # Use lazy-initialized rate limiter
        limiter = get_rate_limiter()
        allowed, info = limiter.is_allowed(
            client_ip=client_ip,
            bucket_name=bucket_name,
            max_tokens=max_tokens,
            refill_rate=refill_rate,
        )

        if not allowed:
            logger.warning(
                "Rate limit exceeded: %s on %s %s (bucket=%s)",
                client_ip,
                request.method,
                request.url.path,
                bucket_name,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please slow down.",
                    "bucket": bucket_name,
                    "retry_after": info.get("Retry-After", "60"),
                },
                headers=info,
            )

        # Process request and add rate limit headers to response
        response = await call_next(request)
        for header, value in info.items():
            response.headers[header] = value

        return response
