import os
import logging

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.database import engine
from app.models.analytics import Base as AnalyticsBase
from app.models.identity import Base as IdentityBase
from app.api.v1.api import api_router
from app.config import get_settings
from app.core.rate_limiter import RateLimitMiddleware

logger = logging.getLogger("sentinel")

# Create schemas if they don't exist
# Note: Production should use Alembic migrations
AnalyticsBase.metadata.create_all(engine)
IdentityBase.metadata.create_all(engine)

app = FastAPI(title="Sentinel - Three Engine System")

settings = get_settings()

# Parse allowed origins from settings
allowed_origins = [
    origin.strip()
    for origin in settings.allowed_origins.split(",")
    if origin.strip()
]

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
    expose_headers=["Content-Length"],
)

# Rate limiting middleware (token bucket per IP)
app.add_middleware(RateLimitMiddleware)


# Security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Ensure unhandled exceptions still return CORS-friendly JSON responses."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)

    origin = request.headers.get("origin", "")
    cors_origin = origin if origin in allowed_origins else allowed_origins[0] if allowed_origins else "*"

    headers = {
        "Access-Control-Allow-Origin": cors_origin,
        "Access-Control-Allow-Credentials": "true",
    }

    # Handle preflight requests
    if request.method == "OPTIONS":
        return JSONResponse(
            status_code=200,
            content={},
            headers=headers,
        )

    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers=headers,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Ensure HTTP exceptions (401, 403, etc.) return CORS-friendly responses."""
    origin = request.headers.get("origin", "")
    cors_origin = origin if origin in allowed_origins else allowed_origins[0] if allowed_origins else "*"

    headers = {
        "Access-Control-Allow-Origin": cors_origin,
        "Access-Control-Allow-Credentials": "true",
    }

    # Merge with existing headers from the exception if any
    if exc.headers:
        headers.update(exc.headers)

    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=headers,
    )


from app.api.websocket import router as ws_router

app.include_router(api_router, prefix="/api/v1")
app.include_router(ws_router, prefix="/ws")


@app.get("/")
def root():
    return {
        "status": "Sentinel",
        "engines": ["Safety Valve", "Talent Scout", "Culture Thermometer"],
    }


@app.get("/health")
def health_check():
    """Health check endpoint for monitoring"""
    return {"status": "healthy", "version": "1.0.0"}
