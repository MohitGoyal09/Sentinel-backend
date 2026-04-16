import os
import logging

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging_config import setup_logging
from app.core.database import engine
from app.models.analytics import Base as AnalyticsBase
from app.models.identity import Base as IdentityBase
from app.models.notification import Base as NotificationBase
from app.api.v1.api import api_router
from app.config import get_settings
from app.core.rate_limiter import RateLimitMiddleware
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.tenant_context import TenantContextMiddleware

settings = get_settings()
setup_logging(settings.log_level)
logger = logging.getLogger("sentinel")

app = FastAPI(title="Sentinel - Three Engine System", redirect_slashes=False)


@app.on_event("startup")
async def startup():
    """Run database migrations on startup."""
    AnalyticsBase.metadata.create_all(engine)
    IdentityBase.metadata.create_all(engine)
    NotificationBase.metadata.create_all(engine)

    # Initialize SSO providers
    from app.services.sso_service import (
        sso_service,
        GoogleSSOProvider,
        AzureADSSOProvider,
        SAMLSSOProvider,
    )

    if settings.google_client_id and settings.google_client_secret:
        sso_service.register_provider(
            "google",
            GoogleSSOProvider(
                client_id=settings.google_client_id,
                client_secret=settings.google_client_secret,
                allowed_domains=settings.google_allowed_domains.split(",")
                if settings.google_allowed_domains
                else [],
            ),
        )
        logger.info("SSO: Google provider registered")

    if settings.azure_client_id and settings.azure_client_secret:
        sso_service.register_provider(
            "azure_ad",
            AzureADSSOProvider(
                client_id=settings.azure_client_id,
                client_secret=settings.azure_client_secret,
                tenant_id=settings.azure_tenant_id,
            ),
        )
        logger.info("SSO: Azure AD provider registered")

    # Only register SAML if configured
    if settings.saml_entity_id:
        sso_service.register_provider(
            "saml",
            SAMLSSOProvider(
                entity_id=settings.saml_entity_id,
                sso_url=settings.saml_sso_url,
                certificate=settings.saml_certificate,
            ),
        )
        logger.info("SSO: SAML provider registered")


# Parse allowed origins from settings
allowed_origins = [
    origin.strip() for origin in settings.allowed_origins.split(",") if origin.strip()
]

# Middleware ordering: FastAPI/Starlette uses LIFO — last added runs first
# on request.  We want the request-phase order to be:
#   1. RequestIDMiddleware   (assign ID first)
#   2. SecurityMiddleware    (input validation)
#   3. TenantContextMiddleware (JWT / auth)
#   4. RateLimitMiddleware   (rate-limit before heavy auth work)
#   5. CORSMiddleware        (runs last on request, first on response)
#
# Therefore we add them in REVERSE order:

from app.middleware.security import SecurityMiddleware

# Added first  -> runs last on request (response-phase first)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-Requested-With",
        "X-Tenant-ID",
        "X-Request-ID",
    ],
    expose_headers=[
        "Content-Length",
        "X-Request-ID",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
    ],
)

# Rate limiting before auth — rejects abusive traffic early
app.add_middleware(RateLimitMiddleware)

# Tenant context (includes JWT verification)
app.add_middleware(TenantContextMiddleware)

# Security headers and input sanitization
app.add_middleware(SecurityMiddleware)

# Request ID first on request (assigned before anything else)
app.add_middleware(RequestIDMiddleware)


# Security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    connect_sources = "'self' http://localhost:* http://127.0.0.1:* ws: wss: " + " ".join(allowed_origins)
    response.headers["Content-Security-Policy"] = (
        f"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src {connect_sources};"
    )
    if settings.environment == "production":
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains; preload"
        )
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Ensure unhandled exceptions still return CORS-friendly JSON responses."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)

    origin = request.headers.get("origin", "")
    headers = {}

    # Only set CORS headers if the request origin matches an allowed origin
    if origin in allowed_origins:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"

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
    headers = {}

    # Only set CORS headers if the request origin matches an allowed origin
    if origin in allowed_origins:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"

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


@app.get("/debug/env")
def debug_env():
    """Temporary debug endpoint — remove after deployment is verified."""
    return {
        "supabase_url_set": bool(settings.supabase_url),
        "supabase_url_prefix": settings.supabase_url[:30] + "..." if settings.supabase_url else "EMPTY",
        "supabase_key_set": bool(settings.supabase_key),
        "supabase_key_length": len(settings.supabase_key) if settings.supabase_key else 0,
        "supabase_service_key_set": bool(settings.supabase_service_key),
        "vault_salt_set": bool(settings.vault_salt),
        "jwt_secret_set": bool(settings.jwt_secret),
        "allowed_origins": settings.allowed_origins,
        "environment": settings.environment,
    }


@app.get("/health")
def health_check():
    """Health check endpoint for monitoring"""
    return {"status": "healthy", "version": "1.0.0"}


@app.get("/ready")
def readiness_check():
    """Readiness probe — verifies DB and Redis are reachable."""
    checks = {"database": "unknown", "redis": "unknown"}

    # Check database connectivity
    try:
        from app.core.database import SessionLocal
        from sqlalchemy import text

        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
        logger.warning("Readiness check: database unreachable")

    # Check Redis connectivity
    try:
        import redis as redis_lib

        r = redis_lib.from_url(settings.redis_url, socket_timeout=2)
        r.ping()
        r.close()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"
        logger.warning("Readiness check: redis unreachable")

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ready" if all_ok else "degraded", "checks": checks},
    )
