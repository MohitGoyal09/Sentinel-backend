import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from uuid import uuid4

from app.core.database import get_db
from app.core.response import success_response, error_response
from app.models.identity import UserIdentity, AuditLog
from app.models.notification import Notification, NotificationPreference
from app.api.deps.auth import get_current_user_identity

logger = logging.getLogger("sentinel.notifications")
router = APIRouter()


@router.get("")
@router.get("/")
async def list_notifications(
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
    unread_only: bool = False,
    limit: int = Query(default=50, le=100),
    offset: int = 0,
):
    """List notifications for the current user."""
    query = db.query(Notification).filter_by(user_hash=user.user_hash)
    if unread_only:
        query = query.filter(Notification.read_at.is_(None))

    total = query.count()
    notifications = (
        query.order_by(Notification.created_at.desc()).offset(offset).limit(limit).all()
    )

    return success_response(
        {
            "notifications": [
                {
                    "id": str(n.id),
                    "type": n.type,
                    "title": n.title,
                    "message": n.message,
                    "data": n.data,
                    "priority": n.priority,
                    "action_url": n.action_url,
                    "read": n.read_at is not None,
                    "read_at": n.read_at.isoformat() if n.read_at else None,
                    "created_at": n.created_at.isoformat() if n.created_at else None,
                }
                for n in notifications
            ],
            "total": total,
            "unread_count": db.query(Notification)
            .filter_by(user_hash=user.user_hash)
            .filter(Notification.read_at.is_(None))
            .count(),
        }
    )


@router.get("/unread-count")
async def get_unread_count(
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """Get count of unread notifications."""
    count = (
        db.query(Notification)
        .filter_by(user_hash=user.user_hash)
        .filter(Notification.read_at.is_(None))
        .count()
    )
    return success_response({"unread_count": count})


@router.put("/{notification_id}/read")
async def mark_as_read(
    notification_id: str,
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """Mark a notification as read."""
    notification = (
        db.query(Notification)
        .filter_by(id=notification_id, user_hash=user.user_hash)
        .first()
    )
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    notification.read_at = datetime.utcnow()
    db.commit()
    return success_response({"message": "Notification marked as read"})


@router.put("/mark-all-read")
async def mark_all_read(
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """Mark all notifications as read."""
    db.query(Notification).filter_by(user_hash=user.user_hash).filter(
        Notification.read_at.is_(None)
    ).update({"read_at": datetime.utcnow()})
    db.commit()
    return success_response({"message": "All notifications marked as read"})


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: str,
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """Delete a notification."""
    notification = (
        db.query(Notification)
        .filter_by(id=notification_id, user_hash=user.user_hash)
        .first()
    )
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    db.delete(notification)
    db.commit()
    return success_response({"message": "Notification deleted"})


@router.get("/preferences")
async def get_preferences(
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """Get notification preferences for the current user."""
    prefs = db.query(NotificationPreference).filter_by(user_hash=user.user_hash).all()
    return success_response(
        [
            {
                "id": str(p.id),
                "channel": p.channel,
                "notification_type": p.notification_type,
                "enabled": p.enabled,
            }
            for p in prefs
        ]
    )


@router.put("/preferences")
async def update_preferences(
    preferences: list[dict],
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    """Update notification preferences."""
    for pref in preferences:
        existing = (
            db.query(NotificationPreference)
            .filter_by(
                user_hash=user.user_hash,
                channel=pref["channel"],
                notification_type=pref["notification_type"],
            )
            .first()
        )
        if existing:
            existing.enabled = pref.get("enabled", True)
        else:
            db.add(
                NotificationPreference(
                    user_hash=user.user_hash,
                    channel=pref["channel"],
                    notification_type=pref["notification_type"],
                    enabled=pref.get("enabled", True),
                )
            )
    db.commit()
    return success_response({"message": "Preferences updated"})


def create_notification(
    db: Session,
    user_hash: str,
    notification_type: str,
    title: str,
    message: str,
    priority: str = "normal",
    action_url: str = None,
    data: dict = None,
    tenant_id: str = None,
):
    """Helper function to create notifications from other endpoints."""
    notification = Notification(
        user_hash=user_hash,
        tenant_id=tenant_id,
        type=notification_type,
        title=title,
        message=message,
        priority=priority,
        action_url=action_url,
        data=data or {},
    )
    db.add(notification)
    db.commit()
    return notification
