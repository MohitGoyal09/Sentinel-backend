"""
Data Ingestion Pipeline API endpoints.
Provides pipeline status, connector health, metrics, and CSV upload.
"""

import csv
import io
import time
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.api.deps import get_db
from app.api.deps.auth import get_current_user_identity, require_role, get_tenant_member
from app.core.security import privacy
from app.models.analytics import Event
from app.models.identity import UserIdentity
from app.models.tenant import TenantMember
from app.services.audit_service import AuditService, AuditAction

logger = logging.getLogger("sentinel.ingestion_api")
router = APIRouter()

# ============================================
# In-Memory Pipeline Metrics (per-process)
# ============================================

_pipeline_metrics = {
    "total_ingested": 0,
    "total_errors": 0,
    "events_by_source": {},
    "recent_events": [],  # last 50 ingested events for live feed
    "pipeline_start_time": datetime.utcnow().isoformat(),
    "stage_metrics": {
        "Collection": {"processed": 0, "error_count": 0, "last_processed_at": None},
        "Validation": {"processed": 0, "error_count": 0, "last_processed_at": None},
        "Privacy Layer": {"processed": 0, "error_count": 0, "last_processed_at": None},
        "Storage": {"processed": 0, "error_count": 0, "last_processed_at": None},
        "Engine Processing": {"processed": 0, "error_count": 0, "last_processed_at": None},
    },
}

EXPECTED_CSV_COLUMNS = {"timestamp", "user_email", "event_type", "source"}


# ============================================
# Models
# ============================================

class ConnectorInfo(BaseModel):
    name: str
    status: str  # connected, pending, disconnected, error
    icon: str
    events_ingested: int = 0
    last_sync: Optional[str] = None
    latency_ms: Optional[float] = None
    description: str = ""


class PipelineStage(BaseModel):
    name: str
    status: str  # active, idle, error
    processed: int = 0
    error_count: int = 0
    last_processed_at: Optional[str] = None
    description: str = ""


class IngestionEvent(BaseModel):
    id: str
    timestamp: str
    source: str
    event_type: str
    user_hash: str
    status: str  # ingested, hashed, error
    latency_ms: float = 0.0


# ============================================
# Endpoints
# ============================================

@router.get("/status")
def get_pipeline_status(db: Session = Depends(get_db), user=Depends(require_role("admin", "manager"))):
    """Get full pipeline status including connectors, stages, metrics."""
    # Query actual DB counts
    try:
        total_events = db.query(func.count(Event.id)).scalar() or 0
        total_users = db.query(func.count(UserIdentity.user_hash)).scalar() or 0
        # Events in last hour
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        recent_count = db.query(func.count(Event.id)).filter(
            Event.timestamp >= one_hour_ago
        ).scalar() or 0
    except Exception:
        total_events = _pipeline_metrics["total_ingested"]
        total_users = 0
        recent_count = 0

    connectors = [
        ConnectorInfo(
            name="Git",
            status="connected",
            icon="git-branch",
            events_ingested=_pipeline_metrics["events_by_source"].get("git", 0) + (total_events // 3),
            last_sync=(datetime.utcnow() - timedelta(minutes=2)).isoformat(),
            latency_ms=8.3,
            description="Commit history, PR reviews, code frequency",
        ),
        ConnectorInfo(
            name="Slack",
            status="connected",
            icon="message-square",
            events_ingested=_pipeline_metrics["events_by_source"].get("slack", 0) + (total_events // 4),
            last_sync=(datetime.utcnow() - timedelta(minutes=5)).isoformat(),
            latency_ms=12.7,
            description="Message patterns, response times, channel activity",
        ),
        ConnectorInfo(
            name="Jira",
            status="connected",
            icon="clipboard-list",
            events_ingested=_pipeline_metrics["events_by_source"].get("jira", 0) + (total_events // 5),
            last_sync=(datetime.utcnow() - timedelta(minutes=8)).isoformat(),
            latency_ms=15.2,
            description="Sprint velocity, ticket lifecycle, workload",
        ),
        ConnectorInfo(
            name="Calendar",
            status="pending",
            icon="calendar",
            events_ingested=0,
            description="Meeting load, focus time, after-hours meetings",
        ),
        ConnectorInfo(
            name="CSV Upload",
            status="connected",
            icon="upload",
            events_ingested=_pipeline_metrics["events_by_source"].get("csv", 0),
            last_sync=datetime.utcnow().isoformat(),
            latency_ms=2.1,
            description="Manual data import for onboarding",
        ),
    ]

    sm = _pipeline_metrics["stage_metrics"]
    pipeline_stages = [
        PipelineStage(
            name="Collection",
            status="active",
            processed=total_events,
            error_count=sm["Collection"]["error_count"],
            last_processed_at=sm["Collection"]["last_processed_at"],
            description="Webhooks & API polling from connected sources",
        ),
        PipelineStage(
            name="Validation",
            status="active",
            processed=total_events,
            error_count=sm["Validation"]["error_count"],
            last_processed_at=sm["Validation"]["last_processed_at"],
            description="Schema validation, deduplication, timestamp normalization",
        ),
        PipelineStage(
            name="Privacy Layer",
            status="active",
            processed=total_users,
            error_count=sm["Privacy Layer"]["error_count"],
            last_processed_at=sm["Privacy Layer"]["last_processed_at"],
            description="HMAC hashing, AES-256 encryption, PII removal",
        ),
        PipelineStage(
            name="Storage",
            status="active",
            processed=total_events,
            error_count=sm["Storage"]["error_count"],
            last_processed_at=sm["Storage"]["last_processed_at"],
            description="Dual-vault architecture (Vault A: analytics, Vault B: identity)",
        ),
        PipelineStage(
            name="Engine Processing",
            status="active",
            processed=total_events,
            error_count=sm["Engine Processing"]["error_count"],
            last_processed_at=sm["Engine Processing"]["last_processed_at"],
            description="Safety Valve, Talent Scout, Culture Thermometer analysis",
        ),
    ]

    return {
        "mode": "simulation",  # or "live" in production
        "connectors": [c.model_dump() for c in connectors],
        "pipeline_stages": [s.model_dump() for s in pipeline_stages],
        "metrics": {
            "total_events": total_events + _pipeline_metrics["total_ingested"],
            "total_users": total_users,
            "events_per_hour": recent_count + _pipeline_metrics.get("events_last_hour", 0),
            "avg_latency_ms": 11.4,
            "error_rate": round(
                _pipeline_metrics["total_errors"]
                / max(_pipeline_metrics["total_ingested"] + total_events, 1)
                * 100,
                2,
            ),
            "uptime_hours": round(
                (datetime.utcnow() - datetime.fromisoformat(_pipeline_metrics["pipeline_start_time"])).total_seconds() / 3600,
                1,
            ),
        },
        "recent_events": _pipeline_metrics["recent_events"][-30:],
    }


@router.post("/upload-csv")
async def upload_csv(file: UploadFile = File(...), db: Session = Depends(get_db), user=Depends(require_role("admin", "manager"))):
    """
    Upload a CSV file to ingest behavioral data.
    Expected columns: timestamp, user_email, event_type, source
    Optional columns: metadata_* (any column prefixed with metadata_ becomes event metadata)
    """
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are accepted")

    # Read and decode
    try:
        content = await file.read()
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded")

    # Parse CSV
    try:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise HTTPException(status_code=400, detail="Empty CSV or missing headers")

        fields = set(reader.fieldnames)
        missing = EXPECTED_CSV_COLUMNS - fields
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required columns: {', '.join(missing)}. Required: {', '.join(EXPECTED_CSV_COLUMNS)}",
            )
    except csv.Error as e:
        raise HTTPException(status_code=400, detail=f"CSV parse error: {str(e)}")

    # Process rows
    ingested = 0
    errors = []
    ingested_events = []

    reader = csv.DictReader(io.StringIO(text))
    for i, row in enumerate(reader, start=2):
        start_time = time.time()
        try:
            ts_raw = row.get("timestamp", "").strip()
            if not ts_raw:
                errors.append(f"Row {i}: missing timestamp")
                continue

            # Try multiple timestamp formats
            ts = None
            for fmt in [
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d",
            ]:
                try:
                    ts = datetime.strptime(ts_raw, fmt)
                    break
                except ValueError:
                    continue
            if ts is None:
                errors.append(f"Row {i}: invalid timestamp '{ts_raw}'")
                continue

            email = row.get("user_email", "").strip()
            if not email:
                errors.append(f"Row {i}: missing user_email")
                continue

            # Privacy layer: hash the email
            user_hash = privacy.hash_identity(email)

            # Ensure user identity exists
            existing = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
            if not existing:
                identity = UserIdentity(
                    user_hash=user_hash,
                    email_encrypted=privacy.encrypt(email),
                    created_at=datetime.utcnow(),
                )
                db.add(identity)

            # Collect metadata columns
            metadata = {}
            for key, val in row.items():
                if key.startswith("metadata_") and val:
                    metadata[key.replace("metadata_", "")] = val

            source = row.get("source", "csv").strip()
            event_type = row.get("event_type", "unknown").strip()

            # Store event
            db_event = Event(
                user_hash=user_hash,
                event_type=event_type,
                timestamp=ts,
                metadata_=metadata if metadata else {"source": source},
            )
            db.add(db_event)
            ingested += 1

            latency = round((time.time() - start_time) * 1000, 2)

            # Track in metrics
            _pipeline_metrics["total_ingested"] += 1
            _pipeline_metrics["events_by_source"]["csv"] = (
                _pipeline_metrics["events_by_source"].get("csv", 0) + 1
            )

            # Update per-stage metrics
            now_iso = datetime.utcnow().isoformat()
            for stage_name in ("Collection", "Validation", "Privacy Layer", "Storage"):
                _pipeline_metrics["stage_metrics"][stage_name]["processed"] += 1
                _pipeline_metrics["stage_metrics"][stage_name]["last_processed_at"] = now_iso

            # Add to recent events feed
            event_record = {
                "id": f"csv-{i}-{int(time.time())}",
                "timestamp": ts.isoformat(),
                "source": source,
                "event_type": event_type,
                "user_hash": user_hash[:8] + "...",
                "status": "ingested",
                "latency_ms": latency,
            }
            _pipeline_metrics["recent_events"].append(event_record)
            ingested_events.append(event_record)

            # Keep only last 100
            if len(_pipeline_metrics["recent_events"]) > 100:
                _pipeline_metrics["recent_events"] = _pipeline_metrics["recent_events"][-100:]

        except Exception as e:
            errors.append(f"Row {i}: {str(e)}")
            _pipeline_metrics["total_errors"] += 1
            _pipeline_metrics["stage_metrics"]["Collection"]["error_count"] += 1

    # Commit all at once
    if ingested > 0:
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail="Failed to persist events")

    # Audit log for CSV upload
    audit = AuditService(db)
    audit.log(
        actor_hash=user.user_hash,
        actor_role=user.role,
        action=AuditAction.CSV_UPLOADED,
        details={
            "filename": file.filename,
            "rows_ingested": ingested,
            "rows_errored": len(errors),
        },
        tenant_id=user.tenant_id,
    )
    db.commit()

    return {
        "success": True,
        "summary": {
            "total_rows": ingested + len(errors),
            "ingested": ingested,
            "errors": len(errors),
            "privacy_hashed": ingested,
        },
        "error_details": errors[:20],  # Return first 20 errors
        "ingested_events": ingested_events[:30],  # Return first 30 for live feed
    }


@router.get("/sample-csv")
def get_sample_csv(user=Depends(get_current_user_identity)):
    """Return a sample CSV template for data upload."""
    return {
        "filename": "sentinel_sample_data.csv",
        "columns": list(EXPECTED_CSV_COLUMNS) + ["metadata_channel", "metadata_files_changed", "risk_signal"],
        "sample_rows": [
            {
                "timestamp": "2024-03-01 09:15:00",
                "user_email": "alex@company.com",
                "event_type": "commit",
                "source": "git",
                "metadata_files_changed": "5",
                "risk_signal": "neutral",
            },
            {
                "timestamp": "2024-03-01 23:45:00",
                "user_email": "alex@company.com",
                "event_type": "commit",
                "source": "git",
                "metadata_files_changed": "12",
                "risk_signal": "negative",
            },
            {
                "timestamp": "2024-03-01 10:00:00",
                "user_email": "sarah@company.com",
                "event_type": "message",
                "source": "slack",
                "metadata_channel": "engineering",
                "risk_signal": "positive",
            },
            {
                "timestamp": "2024-03-01 14:30:00",
                "user_email": "jordan@company.com",
                "event_type": "ticket_completed",
                "source": "jira",
                "risk_signal": "positive",
            },
        ],
        "description": "Upload behavioral events from any source. Required columns: timestamp, user_email, event_type, source. Add metadata_* columns for extra context.",
    }
