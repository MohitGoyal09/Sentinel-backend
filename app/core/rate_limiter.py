"""
Rate Limiting Middleware for Sentinel API
==========================================

Uses a simple in-memory token bucket approach (no Redis dependency required for demo).
Production should use slowapi + Redis for distributed rate limiting.

Limits:
  - General API:     60 requests/minute per IP
  - Auth endpoints:  10 requests/minute per IP (brute-force protection)
  - AI endpoints:    20 requests/minute per IP (LLM cost protection)
  - WebSocket:       5 connections/minute per IP
  - File upload:     10 requests/minute per IP
"""

import time
import logging
from collections import defaultdict
from typing import Dict, Tuple
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
            k for k, (_, last) in self._buckets.items()
            if now - last > max_age
        ]
        for k in stale_keys:
            del self._buckets[k]


# Global instance
rate_limiter = TokenBucket()


# ─── Route Classification ─────────────────────────────────────────────────

def classify_route(path: str) -> Tuple[str, int, float]:
    """
    Classify API route into rate limit bucket.
    Returns (bucket_name, max_tokens, refill_rate).
    """
    path_lower = path.lower()

    if "/auth" in path_lower or "/login" in path_lower or "/register" in path_lower:
        return "auth", 10, 0.17          # 10/min
    elif "/ai" in path_lower or "/ask" in path_lower:
        return "ai", 20, 0.33            # 20/min
    elif "/upload" in path_lower or "/ingestion" in path_lower:
        return "upload", 10, 0.17        # 10/min
    elif "/ws" in path_lower:
        return "websocket", 5, 0.08      # 5/min
    elif "/admin" in path_lower:
        return "admin", 30, 0.5          # 30/min
    else:
        return "general", 60, 1.0        # 60/min


# ─── Middleware ────────────────────────────────────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that applies rate limiting per IP and route bucket."""

    async def dispatch(self, request: Request, call_next):
        # Skip health checks and OPTIONS preflight
        if request.url.path in ("/", "/health") or request.method == "OPTIONS":
            return await call_next(request)

        # Get client IP (respect X-Forwarded-For for proxied setups)
        client_ip = request.headers.get(
            "X-Forwarded-For", request.client.host if request.client else "unknown"
        ).split(",")[0].strip()

        bucket_name, max_tokens, refill_rate = classify_route(request.url.path)

        allowed, info = rate_limiter.is_allowed(
            client_ip=client_ip,
            bucket_name=bucket_name,
            max_tokens=max_tokens,
            refill_rate=refill_rate,
        )

        if not allowed:
            logger.warning(
                "Rate limit exceeded: %s on %s %s (bucket=%s)",
                client_ip, request.method, request.url.path, bucket_name,
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
