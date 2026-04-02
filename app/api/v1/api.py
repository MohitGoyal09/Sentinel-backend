from fastapi import APIRouter
from app.api.v1.endpoints import (
    engines,
    me,
    team,
    admin,
    ai,
    ingestion,
    auth,
    tenants,
    auth_enhanced,
    notifications,
    sso,
    users,
    organizations,
    roi,
    demo,
    analytics,
    tools,
)
from app.api.v1.endpoints.workflows import router as workflows_router
from app.api.v1.endpoints.admin_teams import router as admin_teams_router
from app.api.v1.endpoints.admin_promote import router as admin_promote_router
from app.api.v1.endpoints.identity_reveal import router as identity_reveal_router

api_router = APIRouter()
api_router.include_router(engines.router, prefix="/engines", tags=["Engines"])
api_router.include_router(me.router, prefix="/me", tags=["Me"])
api_router.include_router(team.router, prefix="/team", tags=["Team"])
api_router.include_router(admin.router, prefix="/admin", tags=["Admin"])
api_router.include_router(ai.router, prefix="/ai", tags=["AI"])
api_router.include_router(ingestion.router, prefix="/ingestion", tags=["Ingestion"])
api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(auth_enhanced.router, prefix="/auth", tags=["Auth Enhanced"])
api_router.include_router(tenants.router, prefix="/tenants", tags=["Tenants"])
api_router.include_router(
    notifications.router, prefix="/notifications", tags=["Notifications"]
)
api_router.include_router(sso.router, prefix="/sso", tags=["SSO"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(
    organizations.router, prefix="/organizations", tags=["Organizations"]
)
api_router.include_router(roi.router, prefix="/roi", tags=["ROI"])
api_router.include_router(demo.router, prefix="/demo", tags=["Demo"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["Analytics"])
api_router.include_router(tools.router, prefix="/tools", tags=["External Tools"])
api_router.include_router(workflows_router)
api_router.include_router(admin_teams_router)
api_router.include_router(admin_promote_router)
api_router.include_router(identity_reveal_router)
