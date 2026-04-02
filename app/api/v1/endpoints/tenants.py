import logging
import re
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import uuid4
from datetime import datetime

from app.core.database import get_db
from app.core.response import success_response, error_response
from app.models.identity import UserIdentity
from app.models.tenant import Tenant, TenantMember
from app.api.deps.auth import get_current_user_identity
from app.schemas.tenant import (
    CreateTenantRequest,
    InviteMemberRequest,
    UpdateRoleRequest,
)

logger = logging.getLogger("sentinel.tenants")

router = APIRouter()


def _slugify(name: str) -> str:
    """Convert a name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug[:100]


@router.post("")
@router.post("/")
async def create_tenant(
    body: CreateTenantRequest,
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    # Auto-generate slug if not provided
    slug = body.slug or _slugify(body.name)

    # Check if slug is unique
    existing = db.query(Tenant).filter_by(slug=slug).first()
    if existing:
        return error_response("slug_taken", "A tenant with this slug already exists")

    tenant_id = uuid4()
    tenant = Tenant(
        id=tenant_id,
        name=body.name,
        slug=slug,
        plan=body.plan or "free",
        status="active",
    )
    db.add(tenant)

    member = TenantMember(
        tenant_id=tenant_id,
        user_hash=user.user_hash,
        role="owner",
    )
    db.add(member)
    db.commit()
    db.refresh(tenant)

    return success_response(
        {
            "id": str(tenant.id),
            "name": tenant.name,
            "slug": tenant.slug,
            "plan": tenant.plan,
            "status": tenant.status,
            "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
        }
    )


@router.get("/{tenant_id}")
async def get_tenant(
    tenant_id: str,
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    # Verify user is member
    membership = (
        db.query(TenantMember)
        .filter_by(tenant_id=tenant_id, user_hash=user.user_hash)
        .first()
    )

    if not membership:
        return error_response(
            "not_member", "You are not a member of this tenant", status_code=403
        )

    tenant = db.query(Tenant).filter_by(id=tenant_id).first()
    if not tenant:
        return error_response("not_found", "Tenant not found", status_code=404)

    member_count = db.query(TenantMember).filter_by(tenant_id=tenant_id).count()

    return success_response(
        {
            "id": str(tenant.id),
            "name": tenant.name,
            "slug": tenant.slug,
            "plan": tenant.plan,
            "status": tenant.status,
            "settings": tenant.settings,
            "member_count": member_count,
            "your_role": membership.role,
            "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
            "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None,
        }
    )


@router.post("/{tenant_id}/members")
async def invite_member(
    tenant_id: str,
    body: InviteMemberRequest,
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    # Verify user is admin/owner
    membership = (
        db.query(TenantMember)
        .filter_by(tenant_id=tenant_id, user_hash=user.user_hash)
        .first()
    )

    if not membership or membership.role not in ("owner", "admin"):
        return error_response(
            "forbidden", "Only owners and admins can invite members", status_code=403
        )

    from app.core.security import privacy

    # Find user by email
    invitee_hash = privacy.hash_identity(body.email)
    invitee = db.query(UserIdentity).filter_by(user_hash=invitee_hash).first()

    if not invitee:
        return error_response(
            "user_not_found", "No user found with this email", status_code=404
        )

    # Check if already a member
    existing = (
        db.query(TenantMember)
        .filter_by(tenant_id=tenant_id, user_hash=invitee_hash)
        .first()
    )
    if existing:
        return error_response(
            "already_member", "User is already a member of this tenant"
        )

    new_member = TenantMember(
        tenant_id=tenant_id,
        user_hash=invitee_hash,
        role=body.role or "member",
        invited_by=user.user_hash,
    )
    db.add(new_member)
    db.commit()
    db.refresh(new_member)

    return success_response(
        {
            "id": str(new_member.id),
            "tenant_id": str(new_member.tenant_id),
            "user_hash": new_member.user_hash,
            "role": new_member.role,
            "invited_by": new_member.invited_by,
            "joined_at": new_member.joined_at.isoformat()
            if new_member.joined_at
            else None,
        }
    )


@router.get("/{tenant_id}/members")
async def list_members(
    tenant_id: str,
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    # Verify user is member
    membership = (
        db.query(TenantMember)
        .filter_by(tenant_id=tenant_id, user_hash=user.user_hash)
        .first()
    )

    if not membership:
        return error_response(
            "not_member", "You are not a member of this tenant", status_code=403
        )

    members = db.query(TenantMember).filter_by(tenant_id=tenant_id).all()

    return success_response(
        [
            {
                "id": str(m.id),
                "user_hash": m.user_hash,
                "role": m.role,
                "invited_by": m.invited_by,
                "joined_at": m.joined_at.isoformat() if m.joined_at else None,
            }
            for m in members
        ]
    )


@router.patch("/{tenant_id}/members/{member_id}")
async def update_member_role(
    tenant_id: str,
    member_id: str,
    body: UpdateRoleRequest,
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    # Verify user is admin/owner
    membership = (
        db.query(TenantMember)
        .filter_by(tenant_id=tenant_id, user_hash=user.user_hash)
        .first()
    )

    if not membership or membership.role not in ("owner", "admin"):
        return error_response(
            "forbidden", "Only owners and admins can update roles", status_code=403
        )

    # Validate role
    valid_roles = ("owner", "admin", "member", "viewer")
    if body.role not in valid_roles:
        return error_response(
            "invalid_role", f"Role must be one of: {', '.join(valid_roles)}"
        )

    # Find target member
    target_member = (
        db.query(TenantMember).filter_by(id=member_id, tenant_id=tenant_id).first()
    )

    if not target_member:
        return error_response("not_found", "Member not found", status_code=404)

    # Cannot demote the last owner
    if target_member.role == "owner" and body.role != "owner":
        owner_count = (
            db.query(TenantMember).filter_by(tenant_id=tenant_id, role="owner").count()
        )
        if owner_count <= 1:
            return error_response("last_owner", "Cannot change role of the last owner")

    target_member.role = body.role
    db.commit()
    db.refresh(target_member)

    return success_response(
        {
            "id": str(target_member.id),
            "user_hash": target_member.user_hash,
            "role": target_member.role,
        }
    )


@router.delete("/{tenant_id}/members/{member_id}")
async def remove_member(
    tenant_id: str,
    member_id: str,
    user: UserIdentity = Depends(get_current_user_identity),
    db: Session = Depends(get_db),
):
    # Verify user is admin/owner
    membership = (
        db.query(TenantMember)
        .filter_by(tenant_id=tenant_id, user_hash=user.user_hash)
        .first()
    )

    if not membership or membership.role not in ("owner", "admin"):
        return error_response(
            "forbidden", "Only owners and admins can remove members", status_code=403
        )

    # Find target member
    target_member = (
        db.query(TenantMember).filter_by(id=member_id, tenant_id=tenant_id).first()
    )

    if not target_member:
        return error_response("not_found", "Member not found", status_code=404)

    # Cannot remove the last owner
    if target_member.role == "owner":
        owner_count = (
            db.query(TenantMember).filter_by(tenant_id=tenant_id, role="owner").count()
        )
        if owner_count <= 1:
            return error_response(
                "last_owner", "Cannot remove the last owner of the tenant"
            )

    db.delete(target_member)
    db.commit()

    return success_response({"message": "Member removed successfully"})
