import logging

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Query, status
from sqlalchemy.orm import Session
from typing import List

logger = logging.getLogger("sentinel.engines")

from app.services.safety_valve import SafetyValve
from app.services.talent_scout import TalentScout
from app.services.culture_temp import CultureThermometer
from app.services.simulation import RealTimeSimulator
from app.services.sir_model import predict_contagion_risk
from app.models.analytics import Event
from app.core.vault import VaultManager
from app.api.deps import get_db
from app.core.database import SessionLocal
from app.schemas.engines import (
    CreatePersonaRequest,
    InjectEventRequest,
    AnalyzeTeamRequest,
    ForecastRequest,
    SimulationResponse,
    SafetyValveResponse,
    TalentScoutResponse,
    CultureThermometerResponse,
    RealtimeInjectionResponse,
    UserListResponse,
    RiskHistoryResponse,
    NudgeResponse,
    ActivityEventResponse,
)
from datetime import datetime, timedelta
from app.services.context import ContextEnricher
from typing import Optional
from app.models.identity import UserIdentity
from app.core.security import privacy
from app.services.permission_service import PermissionService, PermissionDenied

from app.api.deps.auth import get_current_user, get_current_user_identity, require_role, get_tenant_member
from app.models.tenant import TenantMember
from app.services.audit_service import AuditService, AuditAction


router = APIRouter()


# Permission check helper
def check_user_data_access(
    db: Session,
    member: TenantMember,
    target_user_hash: str,
) -> None:
    """
    Check if current user has permission to access target user's data.
    Raises HTTPException 403 if access is denied.
    """
    if not member:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    if target_user_hash == "global":
        if member.role not in ("admin", "manager"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions for global access",
            )
        return

    perm_service = PermissionService(db)
    can_view, reason = perm_service.can_view_user_data(db, member, target_user_hash)

    # Log the access attempt
    accessor_hash = getattr(member, "user_hash", None)
    if accessor_hash is not None:
        accessor_hash = str(accessor_hash)
    else:
        accessor_hash = "unknown"
    perm_service.log_data_access(
        db,
        actor_hash=accessor_hash,
        actor_role=member.role,
        target_hash=target_user_hash,
        action="view_engine_data",
        details={
            "granted": can_view,
            "reason": reason,
            "endpoint": "engines",
        },
    )

    if not can_view:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: {reason}",
        )


# Background task wrapper
def run_all_engines(user_hash: str):
    try:
        with SessionLocal() as db:
            SafetyValve(db).analyze(user_hash)
            TalentScout(db).analyze_network()
    except Exception:
        logger.exception("Background engine analysis failed for user_hash=%s", user_hash)


# ============ SIMULATION / PERSONAS ============


@router.post(
    "/personas", response_model=SimulationResponse, status_code=status.HTTP_201_CREATED
)
def create_persona(
    request: CreatePersonaRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    member: TenantMember = Depends(require_role("admin")),
):
    """Create a persona with 30 days of synthetic behavioral data"""
    sim = RealTimeSimulator(db)
    vault = VaultManager(db, db)

    user_hash = vault.store_identity(request.email)
    events = sim.create_persona(request.persona_type, user_hash)

    for event in events:
        db.add(event)

    if request.persona_type in ["sarah_gem", "maria_contagion"]:
        team = ["alex_hash", "sarah_hash", "jordan_hash"]
        edges = sim.create_team_network(team)
        for edge in edges:
            db.add(edge)

    db.commit()

    # Seed 30 days of historical risk data for the velocity chart
    engine = SafetyValve(db)
    engine.seed_risk_history(user_hash, request.persona_type)

    background_tasks.add_task(run_all_engines, user_hash)

    return SimulationResponse(
        success=True,
        data={
            "user_hash": user_hash,
            "events_count": len(events),
            "persona": request.persona_type,
        },
    )


# ============ ENGINE ANALYSIS ============


@router.get("/users/{user_hash}/context")
async def check_user_context(
    user_hash: str,
    timestamp: str = None,
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
    member: TenantMember = Depends(get_tenant_member),
):
    """Check contextual explanation for a specific timestamp"""

    # RBAC Check: Verify user has permission to access this data
    check_user_data_access(db, member, user_hash)

    enricher = ContextEnricher(db)

    # Get email
    user = db.query(UserIdentity).filter_by(user_hash=user_hash).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    email = privacy.decrypt(user.email_encrypted)

    if timestamp:
        try:
            check_time = datetime.fromisoformat(timestamp)
        except ValueError:
            check_time = datetime.utcnow()
    else:
        check_time = datetime.utcnow()

    context = await enricher.is_explained(email, check_time)

    # Envelope pattern manually here, or we could add schema
    return {
        "success": True,
        "data": {"user_hash": user_hash, "timestamp": check_time, "context": context},
    }


@router.get("/users/{user_hash}/safety", response_model=SafetyValveResponse)
def analyze_user_safety(
    user_hash: str,
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
    member: TenantMember = Depends(get_tenant_member),
):
    """Analyze burnout risk for a specific user"""
    # RBAC Check: Verify user has permission to access this data
    check_user_data_access(db, member, user_hash)

    engine = SafetyValve(db)
    result = engine.analyze(user_hash)
    return SafetyValveResponse(success=True, data=result)


@router.get("/users/{user_hash}/talent", response_model=TalentScoutResponse)
def analyze_user_network(
    user_hash: str,
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
    member: TenantMember = Depends(get_tenant_member),
):
    """Analyze network centrality for a specific user"""

    # RBAC Check: Verify user has permission to access this data
    check_user_data_access(db, member, user_hash)

    engine = TalentScout(db)
    result = engine.analyze_network(user_hash, tenant_id=member.tenant_id)
    return TalentScoutResponse(success=True, data=result)


@router.post("/teams/culture", response_model=CultureThermometerResponse)
def analyze_team_culture(
    request: AnalyzeTeamRequest,
    db: Session = Depends(get_db),
    member: TenantMember = Depends(get_tenant_member),
):
    """Analyze culture and contagion risk for a team"""

    team_hashes = request.team_hashes

    if team_hashes:
        if member.role == "employee":
            allowed = {member.user_hash}
            if not set(team_hashes).issubset(allowed):
                raise HTTPException(status_code=403, detail="Employees can only view their own data")
        elif member.role == "manager":
            team_member_hashes = [
                tm.user_hash for tm in
                db.query(TenantMember.user_hash).filter_by(
                    team_id=member.team_id, tenant_id=member.tenant_id
                ).all()
            ] if member.team_id else []
            team = set(team_member_hashes)
            team.add(member.user_hash)
            if not set(team_hashes).issubset(team):
                raise HTTPException(status_code=403, detail="Managers can only view their team members")

    if not team_hashes:
        # Analyze current user's team based on role
        if member.role == "manager":
            team_member_hashes = [
                tm.user_hash for tm in
                db.query(TenantMember.user_hash).filter_by(
                    team_id=member.team_id, tenant_id=member.tenant_id
                ).all()
            ] if member.team_id else []
            team_hashes = [member.user_hash] + team_member_hashes
        elif member.role == "employee":
            team_hashes = [member.user_hash]
        else:
            # Admin sees all users in tenant
            tenant_hashes = [
                tm.user_hash for tm in
                db.query(TenantMember.user_hash).filter_by(tenant_id=member.tenant_id).all()
            ]
            team_hashes = tenant_hashes

    engine = CultureThermometer(db)
    result = engine.analyze_team(team_hashes)
    return CultureThermometerResponse(success=True, data=result)


@router.post("/teams/forecast")
def get_team_forecast(
    request: ForecastRequest,
    db: Session = Depends(get_db),
    member: TenantMember = Depends(get_tenant_member),
):
    """
    Get SIR epidemic forecast for team contagion risk.

    Request body:
    - team_hashes: List of user hashes to analyze
    - days: Forecast horizon (default: 30)
    """
    from app.models.analytics import RiskScore, GraphEdge

    team_hashes = request.team_hashes
    days = request.days

    if team_hashes:
        if member.role == "employee":
            allowed = {member.user_hash}
            if not set(team_hashes).issubset(allowed):
                raise HTTPException(status_code=403, detail="Employees can only view their own data")
        elif member.role == "manager":
            team_member_hashes = [
                tm.user_hash for tm in
                db.query(TenantMember.user_hash).filter_by(
                    team_id=member.team_id, tenant_id=member.tenant_id
                ).all()
            ] if member.team_id else []
            team = set(team_member_hashes)
            team.add(member.user_hash)
            if not set(team_hashes).issubset(team):
                raise HTTPException(status_code=403, detail="Managers can only view their team members")

    if not team_hashes:
        # Analyze current user's team based on role
        if member.role == "manager":
            team_member_hashes = [
                tm.user_hash for tm in
                db.query(TenantMember.user_hash).filter_by(
                    team_id=member.team_id, tenant_id=member.tenant_id
                ).all()
            ] if member.team_id else []
            team_hashes = [member.user_hash] + team_member_hashes
        elif member.role == "employee":
            team_hashes = [member.user_hash]
        else:
            # Admin sees all users in tenant
            team_hashes = [
                tm.user_hash for tm in
                db.query(TenantMember.user_hash).filter_by(tenant_id=member.tenant_id).all()
            ]

    total_members = len(team_hashes)

    risks = db.query(RiskScore).filter(RiskScore.user_hash.in_(team_hashes)).all()
    infected_count = sum(1 for r in risks if r.risk_level in ["ELEVATED", "CRITICAL"])
    avg_risk_score = sum(r.velocity or 0 for r in risks) / len(risks) if risks else 0

    edges = (
        db.query(GraphEdge)
        .filter(
            GraphEdge.source_hash.in_(team_hashes),
            GraphEdge.target_hash.in_(team_hashes),
        )
        .all()
    )
    avg_connections = len(edges) * 2 / total_members if total_members > 0 else 0

    result = predict_contagion_risk(
        total_members=total_members,
        infected_count=infected_count,
        avg_connections=avg_connections,
        avg_risk_score=avg_risk_score,
        days=days,
    )

    return {"success": True, "data": result}


@router.get("/users/{user_hash}/nudge", response_model=NudgeResponse)
def get_nudge(
    user_hash: str,
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
    member: TenantMember = Depends(get_tenant_member),
):
    """Generate a personalized LLM-based nudge for a user based on their risk profile"""
    from app.services.llm import llm_service
    from app.models.identity import UserIdentity as IdentityModel

    # RBAC Check: Verify user has permission to access this data
    check_user_data_access(db, member, user_hash)

    engine = SafetyValve(db)
    analysis = engine.analyze(user_hash)

    risk_level = analysis.get("risk_level", "LOW")
    velocity = analysis.get("velocity", 0)
    belongingness = analysis.get("belongingness_score", 0.5)
    indicators = analysis.get("indicators", {})

    # Get user info for personalization
    user_identity = db.query(IdentityModel).filter_by(user_hash=user_hash).first()
    user_name = "there"
    if user_identity:
        from app.core.security import privacy

        try:
            import re
            email = privacy.decrypt(user_identity.email_encrypted)
            user_name = email.split("@")[0].replace(".", " ").title()
            user_name = re.sub(r'["\n\r{}\\]', '', user_name)[:50]
        except Exception:
            logger.warning("Failed to decrypt email for user_hash=%s; using fallback name", user_hash)

    # Build context for LLM
    indicator_list = []
    if indicators.get("overwork"):
        indicator_list.append("overworking (late nights)")
    if indicators.get("isolation"):
        indicator_list.append("becoming isolated")
    if indicators.get("fragmentation"):
        indicator_list.append("fragmented focus (context switching)")
    if indicators.get("weekend_work"):
        indicator_list.append("working weekends")
    if indicators.get("late_night_pattern"):
        indicator_list.append("late-night work patterns")

    context_str = (
        ", ".join(indicator_list) if indicator_list else "unusual work patterns"
    )

    # Determine nudge type based on risk
    if risk_level == "CRITICAL":
        nudge_type = "urgent_intervention"
    elif risk_level == "ELEVATED":
        nudge_type = "gentle_check_in"
    else:
        # LOW — no nudge needed
        return NudgeResponse(success=True, data=None)

    # Generate LLM-based personalized message
    prompt = f"""
You are a supportive AI assistant helping an employee manager check in with their team member.

Context:
- Team member name: {user_name}
- Risk level: {risk_level} (CRITICAL means immediate attention needed, ELEVATED means concerning trends)
- Work pattern concerns: {context_str}
- Velocity score: {velocity} (higher = more intense recent work)
- Belongingness score: {belongingness} (lower = more isolated)

Generate a warm, empathetic, non-surveillance nudge message (2-3 sentences max) that:
1. Acknowledges their work genuinely
2. Shows care for their wellbeing
3. Suggests one specific action without being preachy

Make it feel human, not corporate. Avoid words like "monitoring", "tracking", "surveillance".
Do NOT mention the specific metrics or scores in the message.
"""

    try:
        llm_message = llm_service.generate_insight(prompt)
        # Clean up the message - remove any quotes if LLM adds them
        llm_message = llm_message.strip().strip('"').strip("'")
    except Exception as e:
        # Fallback to rule-based if LLM fails
        if risk_level == "CRITICAL":
            llm_message = f"Hi {user_name}, we've noticed you've been working intense hours lately. Your wellbeing matters - would you like to block some recovery time this week?"
        else:
            llm_message = f"Hi {user_name}, just checking in. We've noticed {context_str}. How are you feeling about your workload?"

    # Build actions based on risk level
    if risk_level == "CRITICAL":
        actions = [
            {"label": "Block recovery time", "action": "block_recovery"},
            {"label": "Talk to someone", "action": "request_support"},
            {"label": "I'm fine", "action": "dismiss"},
        ]
    else:
        actions = [
            {"label": "Schedule break", "action": "schedule_break"},
            {"label": "Dismiss", "action": "dismiss"},
        ]

    nudge_data = {
        "user_hash": user_hash,
        "nudge_type": nudge_type,
        "message": llm_message,
        "risk_level": risk_level,
        "actions": actions,
    }
    return NudgeResponse(success=True, data=nudge_data)


@router.post("/users/{user_hash}/nudge/dismiss")
def dismiss_nudge(
    user_hash: str,
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
    member: TenantMember = Depends(get_tenant_member),
):
    """Dismiss an active nudge for a user"""
    # RBAC Check: Verify user has permission to access this data
    check_user_data_access(db, member, user_hash)

    from app.models.identity import AuditLog
    from datetime import datetime

    log = AuditLog(
        user_hash=user_hash,
        action="nudge_dismissed",
        details={
            "dismissed_by": member.user_hash,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )
    db.add(log)
    db.commit()

    return {"success": True, "message": "Nudge dismissed"}


@router.post("/users/{user_hash}/nudge/schedule-break")
def schedule_break(
    user_hash: str,
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
    member: TenantMember = Depends(get_tenant_member),
):
    """Schedule a break for a user (logs the action)"""
    # RBAC Check: Verify user has permission to access this data
    check_user_data_access(db, member, user_hash)

    from app.models.identity import AuditLog
    from datetime import datetime, timedelta

    break_time = datetime.utcnow() + timedelta(days=1)

    log = AuditLog(
        user_hash=user_hash,
        action="break_scheduled",
        details={
            "scheduled_by": member.user_hash,
            "scheduled_for": break_time.isoformat(),
            "timestamp": datetime.utcnow().isoformat(),
        },
    )
    db.add(log)
    db.commit()

    return {
        "success": True,
        "message": "Break scheduled",
        "scheduled_time": break_time.isoformat(),
    }


@router.get("/events", response_model=ActivityEventResponse)
def list_events(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    member: TenantMember = Depends(get_tenant_member),
):
    """Get recent activity stream (role-filtered)"""
    query = db.query(Event)

    # Role-based filtering
    if member.role == "employee":
        query = query.filter(Event.user_hash == member.user_hash)
    elif member.role == "manager":
        team_member_hashes = [
            tm.user_hash for tm in
            db.query(TenantMember.user_hash).filter_by(
                team_id=member.team_id, tenant_id=member.tenant_id
            ).all()
        ] if member.team_id else []
        team_hashes = set(team_member_hashes)
        team_hashes.add(member.user_hash)
        query = query.filter(Event.user_hash.in_(team_hashes))
    else:
        # admin: filter by tenant
        tenant_hashes = [
            tm.user_hash for tm in
            db.query(TenantMember.user_hash).filter_by(tenant_id=member.tenant_id).all()
        ]
        query = query.filter(Event.user_hash.in_(tenant_hashes))

    events = (
        query
        .order_by(Event.timestamp.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return ActivityEventResponse(
        success=True, data=[e.to_dict() for e in events], count=len(events)
    )


# ============ PAGINATED USERS LIST ============


@router.get("/users", response_model=UserListResponse)
def list_users(
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 50,
    member: TenantMember = Depends(get_tenant_member),
):
    """
    List all users with their current risk scores (paginated)
    For managers, only returns their team members.
    For employees, returns only themselves.
    For admins, returns all users.

    Query params:
    - skip: Number of records to skip (pagination offset)
    - limit: Maximum number of records to return (default: 50)
    """
    from app.models.analytics import RiskScore
    from app.models.identity import UserIdentity
    from sqlalchemy import desc

    # Get team member hashes first for role-based scoping
    team_member_hashes = None
    if member.role == "manager":
        raw_hashes = [
            tm.user_hash for tm in
            db.query(TenantMember.user_hash).filter_by(
                team_id=member.team_id, tenant_id=member.tenant_id
            ).all()
        ] if member.team_id else []
        team_member_hashes = set(raw_hashes)
        # Add manager themselves
        team_member_hashes.add(member.user_hash)
    elif member.role == "employee":
        team_member_hashes = {member.user_hash}
    else:
        # Admin: scope to tenant
        team_member_hashes = set(
            tm.user_hash for tm in
            db.query(TenantMember.user_hash).filter_by(tenant_id=member.tenant_id).all()
        )

    # Use efficient pagination with JOIN in a single query
    latest_risk = (
        db.query(
            RiskScore.user_hash,
            RiskScore.risk_level,
            RiskScore.velocity,
            RiskScore.confidence,
            RiskScore.updated_at,
        )
        .distinct(RiskScore.user_hash)
        .order_by(RiskScore.user_hash, desc(RiskScore.updated_at))
        .subquery()
    )

    # Build base query
    query = db.query(
        UserIdentity.user_hash,
        UserIdentity.email_encrypted,
        latest_risk.c.risk_level,
        latest_risk.c.velocity,
        latest_risk.c.confidence,
        latest_risk.c.updated_at,
    ).outerjoin(latest_risk, UserIdentity.user_hash == latest_risk.c.user_hash)

    # Apply role-based filtering BEFORE pagination
    if team_member_hashes:
        query = query.filter(UserIdentity.user_hash.in_(team_member_hashes))

    users = query.offset(skip).limit(limit).all()

    result = []
    for user in users:
        user_hash, email_encrypted, risk_level, velocity, confidence, updated_at = user

        # Attempt to derive name from encrypted email
        name = f"User {user_hash[:4]}"
        role = "Engineer"
        try:
            # Try proper decryption
            decrypted = privacy.decrypt(email_encrypted)
            name = decrypted.split("@")[0].title()
        except Exception:
            # Handle mock seeded data (fallback)
            try:
                raw = email_encrypted.decode()
                if "encrypted_" in raw:
                    name = raw.replace("encrypted_", "").split("@")[0].title()
            except Exception:
                pass

        result.append(
            {
                "user_hash": user_hash,
                "name": name,
                "role": role,
                "risk_level": risk_level or "LOW",
                "velocity": velocity or 0.0,
                "confidence": confidence or 0.0,
                "updated_at": updated_at.isoformat() if updated_at else None,
            }
        )

    return UserListResponse(success=True, data=result)


# ============ RISK HISTORY ============


@router.get("/users/{user_hash}/history", response_model=RiskHistoryResponse)
def get_risk_history(
    user_hash: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: UserIdentity = Depends(get_current_user_identity),
    member: TenantMember = Depends(get_tenant_member),
):
    """Get historical risk scores for a user"""
    from app.models.analytics import RiskHistory

    # RBAC Check
    check_user_data_access(db, member, user_hash)

    cutoff = datetime.utcnow() - timedelta(days=days)
    history = (
        db.query(RiskHistory)
        .filter(RiskHistory.user_hash == user_hash)
        .filter(RiskHistory.timestamp >= cutoff)
        .order_by(RiskHistory.timestamp.asc())
        .all()
    )

    return RiskHistoryResponse(
        success=True,
        data={
            "user_hash": user_hash,
            "history": [
                {
                    "timestamp": r.timestamp.isoformat(),
                    "risk_level": r.risk_level,
                    "velocity": r.velocity,
                    "belongingness_score": r.belongingness_score,
                }
                for r in history
            ],
        },
    )


# ============ REAL-TIME EVENTS ============


@router.post("/events/inject", response_model=RealtimeInjectionResponse)
def inject_event(
    request: InjectEventRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    member: TenantMember = Depends(require_role("admin")),
):
    """Inject a real-time event for demo purposes (admin only)"""
    sim = RealTimeSimulator(db)
    vault = VaultManager(db, db)

    # Ensure user exists in our system
    try:
        user_hash = vault.store_identity(request.user_hash)
    except Exception:
        # Fallback: use the provided user_hash directly
        user_hash = request.user_hash

    # Generate a realistic event using the simulation engine
    from app.models.analytics import RiskScore
    current_risk_score = db.query(RiskScore).filter_by(user_hash=user_hash).first()
    current_risk = current_risk_score.risk_level if current_risk_score else "LOW"

    event_data = sim.generate_realtime_event(user_hash, current_risk)
    # Override event type if specified in request
    if request.event_type:
        event_data["event_type"] = request.event_type
    if request.metadata:
        event_data["metadata_"].update(request.metadata)

    event = Event(
        user_hash=event_data["user_hash"],
        timestamp=event_data["timestamp"],
        event_type=event_data["event_type"],
        metadata_=event_data["metadata_"],
    )
    db.add(event)
    db.commit()

    # Trigger background analysis
    background_tasks.add_task(run_all_engines, user_hash)

    return RealtimeInjectionResponse(
        success=True,
        data={
            "event_id": event.id,
            "user_hash": user_hash,
            "event_type": event.event_type,
        },
    )


@router.get("/network/global/talent")
def get_global_talent(
    db: Session = Depends(get_db),
    member: TenantMember = Depends(require_role("manager", "admin")),
):
    """Get global talent network analysis"""
    engine = TalentScout(db)
    return {"success": True, "data": engine.analyze_network(tenant_id=member.tenant_id)}


@router.get("/global/network")
def get_global_network(
    db: Session = Depends(get_db),
    member: TenantMember = Depends(require_role("manager", "admin")),
):
    """Get global network metrics"""
    engine = TalentScout(db)
    return {"success": True, "data": engine.get_network_metrics()}


# ============ ADMIN / SEED ============


@router.post("/users/{user_hash}/seed-history")
def seed_user_history(
    user_hash: str,
    persona_type: str = "alex_burnout",
    db: Session = Depends(get_db),
    member: TenantMember = Depends(require_role("admin")),
):
    """Seed 30 days of historical risk data for an existing user (admin/demo use)"""
    from app.models.analytics import RiskHistory

    # Check if history already exists
    existing = db.query(RiskHistory).filter_by(user_hash=user_hash).count()
    if existing > 5:
        return {
            "success": True,
            "data": {
                "message": f"History already has {existing} records",
                "seeded": False,
            },
        }

    engine = SafetyValve(db)
    engine.seed_risk_history(user_hash, persona_type)
    new_count = db.query(RiskHistory).filter_by(user_hash=user_hash).count()

    audit = AuditService(db)
    audit.log(
        actor_hash=member.user_hash,
        actor_role=member.role,
        action=AuditAction.ENGINE_RECOMPUTED,
        target_hash=user_hash,
        details={"trigger": "manual_seed", "persona_type": persona_type},
        tenant_id=member.tenant_id,
    )
    db.commit()

    return {
        "success": True,
        "data": {"message": f"Seeded {new_count} history records", "seeded": True},
    }


# ============ DASHBOARD AGGREGATES ============


@router.get("/dashboard/summary")
def get_dashboard_summary(
    db: Session = Depends(get_db),
    member: TenantMember = Depends(get_tenant_member),
):
    """Get summary metrics for dashboard - filtered by role"""
    from app.models.analytics import RiskScore

    # Filter based on role
    if member.role == "manager":
        # Get team member hashes for this manager's team
        team_member_hashes = [
            tm.user_hash for tm in
            db.query(TenantMember.user_hash).filter_by(
                team_id=member.team_id, tenant_id=member.tenant_id
            ).all()
        ] if member.team_id else []
        user_filter = [member.user_hash] + team_member_hashes
    elif member.role == "employee":
        user_filter = [member.user_hash]
    else:
        # Admin: scope to tenant
        user_filter = [
            tm.user_hash for tm in
            db.query(TenantMember.user_hash).filter_by(tenant_id=member.tenant_id).all()
        ]

    total_users = len(user_filter)
    risk_scores = (
        db.query(RiskScore).filter(RiskScore.user_hash.in_(user_filter)).all()
    )

    # Count risk levels
    risk_counts = {}
    for r in risk_scores:
        level = r.risk_level or "LOW"
        risk_counts[level] = risk_counts.get(level, 0) + 1

    # Avg velocity
    velocities = [r.velocity for r in risk_scores if r.velocity]
    avg_velocity = sum(velocities) / len(velocities) if velocities else 0.0

    result = {
        "total_users": total_users,
        "risk_distribution": {
            "critical": risk_counts.get("CRITICAL", 0),
            "elevated": risk_counts.get("ELEVATED", 0),
            "low": risk_counts.get("LOW", 0),
        },
        "avg_velocity": round(float(avg_velocity), 2),
        "total_events": len(risk_scores),
    }

    return {"success": True, "data": result}
