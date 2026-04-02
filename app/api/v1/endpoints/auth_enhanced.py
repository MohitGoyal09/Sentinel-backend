import logging
import time
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db, get_supabase_client, get_supabase_admin_client
from app.core.security import privacy
from app.core.response import success_response, error_response
from app.models.identity import UserIdentity, AuditLog
from app.models.tenant import Tenant, TenantMember
from app.api.deps.auth import get_current_user_identity
from app.config import get_settings

logger = logging.getLogger("sentinel.auth")
settings = get_settings()
router = APIRouter()

# In-memory failed login tracker (per IP+email)
_failed_logins: dict[str, list[float]] = defaultdict(list)
_lockouts: dict[str, float] = {}


def _check_rate_limit(key: str) -> bool:
    """Check if login attempt is rate-limited. Returns True if allowed."""
    now = time.time()
    window = 900  # 15 minutes

    # Check lockout
    if key in _lockouts:
        if now < _lockouts[key]:
            return False
        del _lockouts[key]

    # Clean old attempts
    _failed_logins[key] = [t for t in _failed_logins[key] if now - t < window]

    if len(_failed_logins[key]) >= settings.max_login_attempts:
        _lockouts[key] = now + (settings.lockout_duration_minutes * 60)
        return False
    return True


def _record_failed_attempt(key: str):
    """Record a failed login attempt."""
    _failed_logins[key].append(time.time())


@router.get("/sessions")
async def list_sessions(
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """List active sessions for the current user (demo: returns user info)."""
    return success_response(
        {
            "sessions": [
                {
                    "id": "current",
                    "user_hash": user.user_hash,
                    "role": user.role,
                    "created_at": user.created_at.isoformat()
                    if user.created_at
                    else None,
                    "last_active": "now",
                    "device": "Current Browser",
                    "ip": "127.0.0.1",
                }
            ],
            "total": 1,
        }
    )


@router.post("/revoke-session/{session_id}")
async def revoke_session(
    session_id: str,
    user: UserIdentity = Depends(get_current_user_identity),
):
    """Revoke a specific session (demo: always succeeds)."""
    logger.info("Session revoked: user=%s session=%s", user.user_hash, session_id)
    return success_response({"message": f"Session {session_id} revoked"})


@router.post("/revoke-all-sessions")
async def revoke_all_sessions(
    user: UserIdentity = Depends(get_current_user_identity),
):
    """Revoke all sessions except current (demo: logs the action)."""
    logger.info("All sessions revoked for user=%s", user.user_hash)
    return success_response({"message": "All other sessions have been revoked"})


@router.get("/audit-log")
async def get_auth_audit_log(
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
    limit: int = 50,
):
    """Get auth-related audit log entries for the current user."""
    logs = (
        db.query(AuditLog)
        .filter(AuditLog.user_hash == user.user_hash)
        .filter(AuditLog.action.like("auth:%"))
        .order_by(AuditLog.timestamp.desc())
        .limit(limit)
        .all()
    )
    return success_response(
        [
            {
                "id": log.id,
                "action": log.action,
                "details": log.details,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            }
            for log in logs
        ]
    )
