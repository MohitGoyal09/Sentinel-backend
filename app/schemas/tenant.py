from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class CreateTenantRequest(BaseModel):
    name: str
    slug: Optional[str] = None
    plan: Optional[str] = "free"


class InviteMemberRequest(BaseModel):
    email: str
    role: Optional[str] = "member"


class UpdateRoleRequest(BaseModel):
    role: str


class TenantResponse(BaseModel):
    id: str
    name: str
    slug: str
    plan: str
    status: str
    settings: Optional[dict] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TenantMemberResponse(BaseModel):
    id: str
    tenant_id: str
    user_hash: str
    role: str
    invited_by: Optional[str] = None
    joined_at: Optional[datetime] = None
