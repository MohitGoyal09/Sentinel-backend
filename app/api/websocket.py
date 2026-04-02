import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session
from app.services.websocket_manager import manager
from app.api.deps import get_db
from app.models.identity import UserIdentity
from app.core.supabase import get_supabase_client
from datetime import datetime

from app.services.safety_valve import SafetyValve
from app.core.security import privacy

logger = logging.getLogger("sentinel.websocket")
router = APIRouter()


@router.websocket("/{user_hash}")
async def personal_dashboard_ws(
    websocket: WebSocket, user_hash: str, db: Session = Depends(get_db)
):
    """
    WebSocket for individual employee dashboard.
    Receives real-time risk updates.
    """
    # Accept connection FIRST per WebSocket protocol (RFC 6455)
    await websocket.accept()

    # Authenticate via token query parameter
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Authentication required")
        return
    try:
        supabase = get_supabase_client()
        user = supabase.auth.get_user(token)
        if not user or not user.user:
            await websocket.close(code=4001, reason="Invalid token")
            return
    except Exception:
        await websocket.close(code=4001, reason="Authentication failed")
        return

    # Validate user_hash
    if (
        not user_hash
        or user_hash.strip() == ""
        or user_hash == "undefined"
        or user_hash == "null"
        or len(user_hash) > 64
    ):
        await websocket.close(code=4000, reason="Invalid user_hash")
        return

    if user_hash == "global":
        # Global channel is read-only broadcast — no data injection allowed
        logger.info("Global WS channel connected from %s", websocket.client.host if websocket.client else "unknown")
        await manager.connect(websocket, user_hash="global")
        try:
            while True:
                data = await websocket.receive_json()
                if data.get("action") == "ping":
                    await websocket.send_json(
                        {"type": "pong", "timestamp": datetime.utcnow().isoformat()}
                    )
                # Ignore any other actions on global channel (read-only)
        except WebSocketDisconnect:
            manager.disconnect(websocket, user_hash="global")
        return

    # Verify hash exists in database - reject unknown users
    user_exists = db.query(UserIdentity).filter_by(user_hash=user_hash).first()

    if not user_exists:
        logger.warning("WS rejected unknown user_hash: %s", user_hash[:8])
        await websocket.close(code=4001, reason="User not found")
        return

    await manager.connect(websocket, user_hash=user_hash)

    try:
        while True:
            # Keep connection alive, wait for client pings or subscription changes
            data = await websocket.receive_json()

            if data.get("action") == "ping":
                await websocket.send_json(
                    {"type": "pong", "timestamp": datetime.utcnow().isoformat()}
                )

            elif data.get("action") == "request_update":
                # Client asking for immediate refresh
                analysis = SafetyValve(db).analyze(user_hash)
                await websocket.send_json({"type": "manual_refresh", "data": analysis})

    except WebSocketDisconnect:
        manager.disconnect(websocket, user_hash=user_hash)


@router.websocket("/admin/team")
async def admin_dashboard_ws(websocket: WebSocket, db: Session = Depends(get_db)):
    """
    WebSocket for manager dashboard (anonymous aggregated data).
    """
    # Accept connection FIRST per WebSocket protocol (RFC 6455)
    await websocket.accept()

    # Authenticate via token query parameter
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Authentication required")
        return
    try:
        supabase = get_supabase_client()
        auth_user = supabase.auth.get_user(token)
        if not auth_user or not auth_user.user:
            await websocket.close(code=4001, reason="Invalid token")
            return
        # Verify admin role
        user_hash = privacy.hash_identity(auth_user.user.email)
        identity = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
        if not identity or identity.role != "admin":
            await websocket.close(code=4003, reason="Admin access required")
            return
    except Exception:
        await websocket.close(code=4001, reason="Authentication failed")
        return

    await manager.connect(websocket, user_hash=None)  # None = admin channel

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("action") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_hash=None)
