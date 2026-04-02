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


class RedisTokenBucket:
    """Redis-backed distributed rate limiter with in-memory fallback."""

    def __init__(self, redis_url: Optional[str] = None):
        self.redis = None
        if redis_url:
            try:
                import redis

                self.redis = redis.from_url(redis_url)
                # Verify connection
                self.redis.ping()
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
            pipe = self.redis.pipeline()
            # Get current state
            pipe.hgetall(key)
            result = pipe.execute()
            data = result[0] or {}

            tokens = float(data.get(b"tokens", max_tokens))
            last_refill = float(data.get(b"last_refill", now))

            elapsed = now - last_refill
            tokens = min(max_tokens, tokens + elapsed * refill_rate)

            info = {
                "X-RateLimit-Limit": str(max_tokens),
                "X-RateLimit-Remaining": str(max(0, int(tokens) - 1)),
                "X-RateLimit-Bucket": bucket_name,
            }

            if tokens < 1.0:
                retry_after = (1.0 - tokens) / refill_rate
                info["Retry-After"] = str(int(retry_after) + 1)
                self.redis.hset(key, mapping={"tokens": tokens, "last_refill": now})
                self.redis.expire(key, 300)
                return False, info

            tokens -= 1.0
            self.redis.hset(key, mapping={"tokens": tokens, "last_refill": now})
            self.redis.expire(key, 300)
            return True, info

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

        # Get client IP (respect X-Forwarded-For for proxied setups)
        client_ip = (
            request.headers.get(
                "X-Forwarded-For", request.client.host if request.client else "unknown"
            )
            .split(",")[0]
            .strip()
        )

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
