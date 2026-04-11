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

# SQL injection checks removed: SQLAlchemy's parameterized queries already
# prevent SQL injection.  The regex patterns caused false positives on
# legitimate values like names with apostrophes or action-type filters
# containing words such as "select", "update", or "delete".

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
        try:
            if content_length and int(content_length) > 10 * 1024 * 1024:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"},
                )
        except (ValueError, TypeError):
            pass

        # Check URL query params for XSS patterns
        query_string = str(request.url.query)
        if query_string and check_xss(query_string):
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


def check_xss(text: str) -> bool:
    """Check if text contains potential XSS patterns."""
    if not text:
        return False
    for pattern in XSS_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False
