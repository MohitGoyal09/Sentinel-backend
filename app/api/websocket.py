from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session
from app.services.websocket_manager import manager
from app.api.deps import get_db
from app.models.identity import UserIdentity
from datetime import datetime

from app.services.safety_valve import SafetyValve
from app.core.security import privacy

router = APIRouter()


@router.websocket("/{user_hash}")
async def personal_dashboard_ws(
    websocket: WebSocket, user_hash: str, db: Session = Depends(get_db)
):
    """
    WebSocket for individual employee dashboard.
    Receives real-time risk updates.
    """
    print(
        f"WS Connection attempt for user_hash: '{user_hash}' (type: {type(user_hash)})"
    )

    # Accept connection FIRST per WebSocket protocol (RFC 6455)
    await websocket.accept()

    # Validate after accepting
    if (
        not user_hash
        or user_hash.strip() == ""
        or user_hash == "undefined"
        or user_hash == "null"
    ):
        print(f"WS Rejected: Invalid user_hash: '{user_hash}'")
        await websocket.close(code=4000, reason="Invalid user_hash")
        return

    if user_hash == "global":
        await manager.connect(websocket, user_hash="global")
        try:
            while True:
                data = await websocket.receive_json()
                if data.get("action") == "ping":
                    await websocket.send_json(
                        {"type": "pong", "timestamp": datetime.utcnow().isoformat()}
                    )
        except WebSocketDisconnect:
            manager.disconnect(websocket, user_hash="global")
        return

    # Verify hash exists (lightweight auth)
    user_exists = db.query(UserIdentity).filter_by(user_hash=user_hash).first()

    if not user_exists:
        # Auto-provision user identity if not exists (for demo/development)
        # In production, you might want to reject instead
        print(f"WS Auto-provisioning user: {user_hash}")
        try:
            # Try to derive email from hash (for demo purposes)
            # In real app, this would require proper authentication
            demo_email = f"user_{user_hash[:8]}@demo.local"
            user_exists = UserIdentity(
                user_hash=user_hash,
                email_encrypted=privacy.encrypt(demo_email),
                created_at=datetime.utcnow(),
            )
            db.add(user_exists)
            db.commit()
            print(f"WS Created new user identity for: {user_hash}")
        except Exception as e:
            db.rollback()
            print(f"WS Failed to auto-provision user {user_hash}: {e}")
            await websocket.close(code=4001, reason="Invalid user")
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
async def admin_dashboard_ws(websocket: WebSocket):
    """
    WebSocket for manager dashboard (anonymous aggregated data).
    """
    # Accept connection FIRST per WebSocket protocol (RFC 6455)
    await websocket.accept()
    await manager.connect(websocket, user_hash=None)  # None = admin channel

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("action") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_hash=None)
