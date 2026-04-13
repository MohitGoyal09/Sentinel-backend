from sqlalchemy.orm import Session
from datetime import datetime
from app.models.identity import UserIdentity, AuditLog
from app.core.security import privacy

class VaultManager:
    """Two-Vault Architecture Implementation"""
    
    def __init__(self, analytics_db: Session, identity_db: Session):
        self.a_db = analytics_db
        self.i_db = identity_db
    
    def store_identity(self, email: str, tenant_id=None) -> str:
        """
        Vault B: Store encrypted identity mapping
        Returns: user_hash for Vault A usage
        """
        user_hash = privacy.hash_identity(email)

        # Check if exists in Vault B
        existing = self.i_db.query(UserIdentity).filter_by(user_hash=user_hash).first()
        if not existing:
            identity = UserIdentity(
                user_hash=user_hash,
                email_encrypted=privacy.encrypt(email),
                # slack_id_encrypted defaults to None initially
                created_at=datetime.utcnow(),
                tenant_id=tenant_id,
            )
            self.i_db.add(identity)
            self.i_db.flush()
        
        return user_hash
    
    def resolve_and_notify(self, user_hash: str, message: str) -> bool:
        """Vault B resolves hash and sends notification"""
        user = self.i_db.query(UserIdentity).filter_by(user_hash=user_hash).first()
        if not user:
            return False
        
        # Decrypt for notification (simulated)
        # email = privacy.decrypt(user.email_encrypted)
        
        # Audit log (hash only)
        log = AuditLog(
            user_hash=user_hash,
            action="nudge_sent",
            details={"message_preview": message[:50]}
        )
        self.i_db.add(log)
        self.i_db.commit()
        
        return True
    
    def delete_user_data(self, user_hash: str):
        """Right to be forgotten"""
        # Delete from Vault A (Analytics)
        # Using late imports to avoid circular dependencies if models import logic
        from app.models.analytics import Event, RiskScore, GraphEdge, CentralityScore
        
        self.a_db.query(Event).filter_by(user_hash=user_hash).delete()
        self.a_db.query(RiskScore).filter_by(user_hash=user_hash).delete()
        self.a_db.query(GraphEdge).filter(
            (GraphEdge.source_hash == user_hash) | (GraphEdge.target_hash == user_hash)
        ).delete()
        self.a_db.query(CentralityScore).filter_by(user_hash=user_hash).delete()
        
        # Delete from Vault B (Identity)
        self.i_db.query(UserIdentity).filter_by(user_hash=user_hash).delete()
        # Anonymize audit logs instead of deleting -- audit trail is immutable
        self.i_db.query(AuditLog).filter(AuditLog.actor_hash == user_hash).update(
            {"actor_hash": "DELETED"}, synchronize_session=False
        )
        self.i_db.query(AuditLog).filter(AuditLog.user_hash == user_hash).update(
            {"user_hash": "DELETED"}, synchronize_session=False
        )
        
        self.a_db.commit()
        self.i_db.commit()
