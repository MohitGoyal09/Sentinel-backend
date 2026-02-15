from typing import Dict, List, Optional
from fastapi import WebSocket, WebSocketDisconnect
import json
from datetime import datetime

class ConnectionManager:
    """Manages WebSocket connections for real-time updates"""
    
    def __init__(self):
        # user_hash -> List[WebSocket]
        self.active_connections: Dict[str, List[WebSocket]] = {}
        # Admin/manager dashboards
        self.admin_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket, user_hash: Optional[str] = None):
        await websocket.accept()
        if user_hash:
            if user_hash not in self.active_connections:
                self.active_connections[user_hash] = []
            self.active_connections[user_hash].append(websocket)
        else:
            self.admin_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket, user_hash: Optional[str] = None):
        if user_hash and user_hash in self.active_connections:
            if websocket in self.active_connections[user_hash]:
                self.active_connections[user_hash].remove(websocket)
            if not self.active_connections[user_hash]:
                del self.active_connections[user_hash]
        elif websocket in self.admin_connections:
            self.admin_connections.remove(websocket)
    
    async def send_personal_message(self, message: dict, user_hash: str):
        """Send update to specific user's dashboard"""
        if user_hash in self.active_connections:
            dead_connections = []
            for connection in self.active_connections[user_hash]:
                try:
                    await connection.send_json(message)
                except Exception:
                    dead_connections.append(connection)
            
            # Cleanup dead connections
            for dead in dead_connections:
                if dead in self.active_connections[user_hash]:
                    self.active_connections[user_hash].remove(dead)
    
    async def broadcast_to_admins(self, message: dict):
        """Send team-level updates to manager dashboards"""
        dead_connections = []
        for connection in self.admin_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)
        
        for dead in dead_connections:
            self.admin_connections.remove(dead)
    
    async def broadcast_risk_update(self, user_hash: str, risk_data: dict):
        """Broadcast when risk score changes"""
        payload = {
            "type": "risk_update",
            "user_hash": user_hash,
            "timestamp": datetime.utcnow().isoformat(),
            "data": risk_data
        }
        
        # Send to specific user
        await self.send_personal_message(payload, user_hash)
        
        # Send anonymized version to admins (if critical)
        if risk_data.get("risk_level") in ["CRITICAL", "ELEVATED"]:
            admin_payload = {
                "type": "team_alert",
                "anonymous_id": user_hash[:8] + "...",  # Partial hash for admin view
                "risk_level": risk_data.get("risk_level"),
                "velocity": risk_data.get("velocity"),
                "timestamp": datetime.utcnow().isoformat()
            }
            await self.broadcast_to_admins(admin_payload)

manager = ConnectionManager()
