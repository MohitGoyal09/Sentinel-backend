from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session
from app.services.websocket_manager import manager
from app.api.deps import get_db
from app.models.identity import UserIdentity
from datetime import datetime

from app.services.safety_valve import SafetyValve

router = APIRouter()

@router.websocket("/{user_hash}")
async def personal_dashboard_ws(websocket: WebSocket, user_hash: str, db: Session = Depends(get_db)):
    """
    WebSocket for individual employee dashboard.
    Receives real-time risk updates.
    """
    # Verify hash exists (lightweight auth)
    user_exists = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
    if not user_exists:
        await websocket.close(code=4001, reason="Invalid user")
        return
    
    await manager.connect(websocket, user_hash=user_hash)
    
    try:
        while True:
            # Keep connection alive, wait for client pings or subscription changes
            data = await websocket.receive_json()
            
            if data.get("action") == "ping":
                await websocket.send_json({"type": "pong", "timestamp": datetime.utcnow().isoformat()})
            
            elif data.get("action") == "request_update":
                # Client asking for immediate refresh
                analysis = SafetyValve(db).analyze(user_hash)
                await websocket.send_json({
                    "type": "manual_refresh",
                    "data": analysis
                })
                
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_hash=user_hash)

@router.websocket("/admin/team")
async def admin_dashboard_ws(websocket: WebSocket):
    """
    WebSocket for manager dashboard (anonymous aggregated data).
    """
    await manager.connect(websocket, user_hash=None)  # None = admin channel
    
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("action") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_hash=None)
