import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import engine
from app.models.analytics import Base as AnalyticsBase
from app.models.identity import Base as IdentityBase
from app.api.v1.api import api_router
from app.config import get_settings

# Create schemas if they don't exist
# Note: Production should use Alembic migrations
AnalyticsBase.metadata.create_all(engine)
IdentityBase.metadata.create_all(engine)

app = FastAPI(title="Sentinel - Three Engine System")

# Read allowed origins from Settings (sourced from .env ALLOWED_ORIGINS)
settings = get_settings()
allowed_origins = [origin.strip() for origin in settings.allowed_origins.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

from app.api.websocket import router as ws_router

app.include_router(api_router, prefix="/api/v1")
app.include_router(ws_router, prefix="/ws")

@app.get("/")
def root():
    return {"status": "Sentinel", "engines": ["Safety Valve", "Talent Scout", "Culture Thermometer"]}

@app.get("/health")
def health_check():
    """Health check endpoint for monitoring"""
    return {"status": "healthy", "version": "1.0.0"}
