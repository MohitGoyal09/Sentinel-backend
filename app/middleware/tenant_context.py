from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import logging

from app.config import get_settings
from app.core.supabase import get_supabase_client

logger = logging.getLogger("sentinel.tenant")

settings = get_settings()


class TenantContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        tenant_id = None

        # Try to extract tenant_id from JWT token in Authorization header
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]
            try:
                # SECURITY: Verify JWT signature before extracting claims
                # Uses Supabase client to verify token authenticity
                supabase = get_supabase_client()
                response = supabase.auth.get_user(token)

                if response and response.user:
                    # Extract tenant_id from verified user metadata
                    # Check user_metadata first, then app_metadata
                    user_metadata = response.user.user_metadata or {}
                    app_metadata = response.user.app_metadata or {}
                    tenant_id = user_metadata.get("tenant_id") or app_metadata.get("tenant_id")
            except Exception as e:
                # Log verification failure but don't break the request flow
                logger.debug("Failed to verify JWT and extract tenant: %s", e)

        request.state.tenant_id = tenant_id

        response = await call_next(request)
        return response
