import logging
from sqlalchemy.orm import Session
from datetime import datetime
from app.services.slack import slack_service
from app.services.context import ContextEnricher
from app.core.security import privacy
from app.models.identity import UserIdentity, AuditLog
from app.models.notification import Notification
from app.models.tenant import TenantMember
from app.services.websocket_manager import manager
from typing import Optional

logger = logging.getLogger("sentinel.nudge_dispatcher")

class NudgeDispatcher:
    """
    Orchestrates the full intervention pipeline:
    1. Check context (is this explained?)
    2. Generate appropriate message
    3. Send via Slack (Mock or Real)
    4. Audit log (AuditLog)
    5. WebSocket notify (Frontend)
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.context = ContextEnricher(db)
    
    async def dispatch(self, user_hash: str, risk_data: dict) -> bool:
        """
        Main entry point: Decide if/what/when to send nudge
        """
        risk_level = risk_data.get("risk_level")
        
        # Don't nudge if calibrating or low
        if risk_level in ["INSUFFICIENT_DATA", "LOW"]:
            return False
        
        # Get user identity from Vault B
        user = self.db.query(UserIdentity).filter_by(user_hash=user_hash).first()
        if not user:
            logger.warning("No identity found for hash %s", user_hash)
            return False
        
        email = privacy.decrypt(user.email_encrypted)
        
        # Check context (don't nudge if on-call)
        now = datetime.utcnow()
        context_check = await self.context.is_explained(email, now)
        
        if context_check["is_explained"] and risk_level != "CRITICAL":
            # If explained and not critical, skip nudge but log
            self._log_skip(user_hash, context_check["explanation_type"])
            return False
        
        # Generate message based on risk level
        message = self._generate_message(risk_data, context_check)
        
        # Send via Slack
        sent = await slack_service.send_nudge(email, message, risk_level)

        if sent:
            # Audit log in Vault B
            self._audit_log(user_hash, risk_level, message, context_check)

            # WebSocket notify frontend
            await manager.broadcast_risk_update(user_hash, {
                **risk_data,
                "nudge_sent": True,
                "nudge_message": message[:100]  # Preview only
            })

        # Create in-app notification regardless of Slack outcome
        # Look up tenant_id from the user's TenantMember record
        tenant_member = self.db.query(TenantMember).filter_by(user_hash=user_hash).first()
        tenant_id = tenant_member.tenant_id if tenant_member else None
        self._create_in_app_notification(user_hash, risk_level, tenant_id=tenant_id)

        return sent or False
    
    def _create_in_app_notification(self, user_hash: str, risk_level: str, tenant_id=None) -> None:
        """Create an in-app Notification record for the employee based on risk level."""
        if risk_level == "CRITICAL":
            title = "Wellness Check"
            message = (
                "Your work patterns show signs of elevated stress. "
                "Consider discussing with your manager or taking a break."
            )
            priority = "critical"
        else:
            title = "Pattern Change Detected"
            message = (
                "We noticed changes in your work patterns. "
                "This is just a heads-up."
            )
            priority = "high"

        notification = Notification(
            user_hash=user_hash,
            tenant_id=tenant_id,
            type="activity",
            title=title,
            message=message,
            priority=priority,
            action_url="/ask-sentinel",
        )
        self.db.add(notification)
        self.db.commit()

    def _generate_message(self, risk_data: dict, context: dict) -> str:
        """Generate empathetic, non-surveillance language"""
        velocity = risk_data.get("velocity", 0)
        belongingness = risk_data.get("belongingness_score", 0.5)
        
        if context["is_explained"]:
            # Even if explained but CRITICAL, gentle nudge
            return (
                "We noticed you've been covering on-call or sprint duties with some intense hours. "
                "Even heroic efforts need recovery—consider blocking Friday for rest?"
            )
        
        if velocity > 2.5:
            return (
                "Your focus sessions have been extending later than usual over the past 2 weeks. "
                "To keep your edge, would you like me to block tomorrow morning for deep recovery?"
            )
        elif belongingness < 0.4:
            return (
                "You've been heads-down on complex work. "
                "Consider joining the team sync tomorrow to reconnect? "
                "Sometimes the hardest problems need fresh perspective."
            )
        else:
            return (
                "We detected some after-hours activity. "
                "Just checking in—everything okay? "
                "Reply here if you need resources or want to talk."
            )
    
    def _audit_log(self, user_hash: str, risk_level: str, message: str, context: dict):
        """Immutable audit trail in Vault B"""
        log = AuditLog(
            user_hash=user_hash,
            action="nudge_sent",
            details={
                "risk_level": risk_level,
                "message_preview": message[:50],
                "had_context": context["is_explained"],
                "context_type": context.get("explanation_type"),
                "timestamp": datetime.utcnow().isoformat()
            }
        )
        self.db.add(log)
        self.db.commit()
    
    def _log_skip(self, user_hash: str, reason: str):
        """Log that we skipped nudging due to context"""
        log = AuditLog(
            user_hash=user_hash,
            action="nudge_skipped",
            details={
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
        self.db.add(log)
        self.db.commit()
