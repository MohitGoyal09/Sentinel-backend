"""
Authentication dependency for FastAPI endpoints.
Verifies Supabase JWT tokens from Authorization header.
"""

from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.core.supabase import get_supabase_client
from app.core.database import get_db
from app.models.identity import UserIdentity, AuditLog
from app.models.tenant import TenantMember
from app.core.security import privacy
from app.services.permission_service import PermissionService, UserRole

security = HTTPBearer()
security_optional = HTTPBearer(auto_error=False)


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_optional),
    db: Session = Depends(get_db),
) -> Optional[UserIdentity]:
    """
    Get current user identity if token is present, otherwise return None.
    Does not raise 401.
    """
    if not credentials:
        return None

    try:
        token = credentials.credentials
        supabase = get_supabase_client()
        response = supabase.auth.get_user(token)

        if not response or not response.user:
            return None

        # Get user hash from email
        user_hash = privacy.hash_identity(response.user.email)

        # Fetch full identity
        user = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
        return user
    except Exception:
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Validate JWT token and return user data.

    Usage:
        @router.get("/protected")
        def protected_route(user: dict = Depends(get_current_user)):
            return {"user_id": user["id"]}
    """
    token = credentials.credentials

    try:
        supabase = get_supabase_client()
        # Verify token by getting user - this validates the JWT
        response = supabase.auth.get_user(token)

        if not response or not response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            )

        return {
            "id": response.user.id,
            "email": response.user.email,
            "role": response.user.role,
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
        )


def get_current_user_identity(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> UserIdentity:
    """
    Get the current user's identity from the database (Vault B).
    Includes audit logging for auth events.
    """
    token = credentials.credentials

    try:
        supabase = get_supabase_client()
        response = supabase.auth.get_user(token)

        if not response or not response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            )

        # Get user hash from email
        user_hash = privacy.hash_identity(response.user.email)

        # Fetch full identity from database
        user = db.query(UserIdentity).filter_by(user_hash=user_hash).first()

        if not user:
            # Auto-create identity for new SSO/OAuth users, always defaulting to employee
            user = UserIdentity(
                user_hash=user_hash,
                email_encrypted=privacy.encrypt(response.user.email),
                role=UserRole.EMPLOYEE.value,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            # Log the auto-creation
            audit = AuditLog(
                user_hash=user_hash,
                action="auth:auto_identity_created",
                details={
                    "email_domain": response.user.email.split("@")[1]
                    if "@" in response.user.email
                    else "unknown",
                    "auto_role": UserRole.EMPLOYEE.value,
                },
            )
            db.add(audit)
            db.commit()
        return user
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
        )


def get_permission_service(db: Session = Depends(get_db)) -> PermissionService:
    """
    Get the permission service for RBAC checks.
    """
    return PermissionService(db)


def get_tenant_member(
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
) -> TenantMember:
    """Get the current user's TenantMember record for their active tenant."""
    if not user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not associated with any organization",
        )

    member = (
        db.query(TenantMember)
        .filter_by(tenant_id=user.tenant_id, user_hash=user.user_hash)
        .first()
    )

    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a member of this organization",
        )

    return member


def require_role(*roles: str):
    """
    Dependency factory to require specific roles.
    Reads role from TenantMember (not UserIdentity).

    Usage:
        @router.get("/admin-only")
        def admin_route(member: TenantMember = Depends(require_role("admin"))):
            return {"message": "Admin access granted"}
    """

    def role_checker(
        member: TenantMember = Depends(get_tenant_member),
    ) -> TenantMember:
        if member.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {', '.join(roles)}",
            )
        return member

    return role_checker


def check_permission_to_view_user(
    target_user_hash: str,
    current_user: UserIdentity = Depends(get_current_user_identity),
    member: TenantMember = Depends(get_tenant_member),
    db: Session = Depends(get_db),
) -> tuple[UserIdentity, bool, str]:
    """
    Check if current user has permission to view target user's data.
    Returns (current_user, can_view, reason)

    Uses TenantMember for role checks (Phase 2).
    """
    permission_service = PermissionService(db)
    can_view, reason = permission_service.can_view_user_data(
        db, member, target_user_hash
    )

    # Log the access attempt
    PermissionService.log_data_access(
        db,
        actor_hash=member.user_hash,
        actor_role=member.role,
        target_hash=target_user_hash,
        action="view_attempt",
        tenant_id=str(member.tenant_id),
        details={
            "granted": can_view,
            "reason": reason,
        },
    )

    return current_user, can_view, reason
