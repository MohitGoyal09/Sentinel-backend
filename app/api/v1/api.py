from fastapi import APIRouter
from app.api.v1.endpoints import engines, me, team, admin, ai, ingestion

api_router = APIRouter()
api_router.include_router(engines.router, prefix="/engines", tags=["Engines"])
api_router.include_router(me.router, prefix="/me", tags=["Me"])
api_router.include_router(team.router, prefix="/team", tags=["Team"])
api_router.include_router(admin.router, prefix="/admin", tags=["Admin"])
api_router.include_router(ai.router, prefix="/ai", tags=["AI"])
api_router.include_router(ingestion.router, prefix="/ingestion", tags=["Ingestion"])
