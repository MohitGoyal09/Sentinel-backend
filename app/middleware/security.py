"""
Security middleware for Sentinel.
Implements OWASP security headers, input sanitization, and request validation.
"""

import re
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("sentinel.security")

# Patterns that indicate potential attacks
SQL_INJECTION_PATTERNS = [
    r"(\b(union|select|insert|update|delete|drop|alter|create|exec)\b)",
    r"(--|;|'|\")",
    r"(\b(or|and)\b\s+\d+\s*=\s*\d+)",
]

XSS_PATTERNS = [
    r"(<script[^>]*>)",
    r"(javascript:)",
    r"(on\w+\s*=)",
    r"(<iframe[^>]*>)",
]


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip security checks for static files, health checks, and CORS preflight
        path = request.url.path
        if path in ("/", "/health", "/ready") or path.startswith("/static"):
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Validate request body size (max 10MB)
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 10 * 1024 * 1024:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request body too large"},
            )

        # Check URL query params for injection patterns
        query_string = str(request.url.query)
        if query_string and (check_sql_injection(query_string) or check_xss(query_string)):
            logger.warning("Suspicious query string blocked: %s", path)
            return JSONResponse(
                status_code=400,
                content={"detail": "Invalid request parameters"},
            )

        # Process request
        response = await call_next(request)
        return response


def sanitize_input(text: str) -> str:
    """Basic input sanitization."""
    if not text:
        return text
    # Remove null bytes
    text = text.replace("\x00", "")
    # Limit length
    if len(text) > 10000:
        text = text[:10000]
    return text


def check_sql_injection(text: str) -> bool:
    """Check if text contains potential SQL injection patterns."""
    if not text:
        return False
    text_lower = text.lower()
    for pattern in SQL_INJECTION_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


def check_xss(text: str) -> bool:
    """Check if text contains potential XSS patterns."""
    if not text:
        return False
    for pattern in XSS_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False
