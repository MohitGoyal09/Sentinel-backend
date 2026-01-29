from fastapi import APIRouter
from app.api.v1.endpoints import engines

api_router = APIRouter()
api_router.include_router(engines.router, prefix="/engines", tags=["Engines"])
