# Sentinel Backend

AI-powered employee insight engine. FastAPI + PostgreSQL + Redis backend implementing the Three Engines architecture with privacy-by-design principles.

---

## Table of Contents

- [Backend Architecture Overview](#backend-architecture-overview)
- [API Endpoints Summary](#api-endpoints-summary)
- [Database Schema Overview](#database-schema-overview)
- [Authentication and Security Model](#authentication-and-security-model)
- [Environment Variables](#environment-variables)
- [Running the Application](#running-the-application)
- [Running Tests](#running-tests)
- [Key Modules Explained](#key-modules-explained)

---

## Backend Architecture Overview

### Directory Structure

```
backend/
├── app/
│   ├── api/
│   │   ├── v1/
│   │   │   ├── endpoints/          # One router file per domain
│   │   │   │   ├── admin.py        # System admin (health, audit logs, user mgmt)
│   │   │   │   ├── ai.py           # AI chat, narrative reports, semantic query
│   │   │   │   ├── analytics.py    # Team energy heatmap
│   │   │   │   ├── auth.py         # Login, register, refresh, logout
│   │   │   │   ├── auth_enhanced.py# MFA, passkeys, session management
│   │   │   │   ├── demo.py         # Demo scenarios and seeding
│   │   │   │   ├── engines.py      # Three Engines: Safety Valve, Talent Scout, Culture Therm.
│   │   │   │   ├── ingestion.py    # Bulk data ingestion (CSV, webhooks)
│   │   │   │   ├── me.py           # Employee self-service (consent, pause, delete)
│   │   │   │   ├── notifications.py# In-app notification CRUD
│   │   │   │   ├── organizations.py# Organization management
│   │   │   │   ├── roi.py          # ROI/burnout cost calculator
│   │   │   │   ├── sso.py          # SSO initiation and callbacks
│   │   │   │   ├── team.py         # Team analytics and comparisons
│   │   │   │   ├── tenants.py      # Multi-tenant management
│   │   │   │   ├── tools.py        # External tool execution via Composio
│   │   │   │   └── users.py        # User search and directory
│   │   │   └── api.py              # Router aggregation
│   │   ├── deps/
│   │   │   └── auth.py             # Auth dependencies and role guards
│   │   └── websocket.py            # WebSocket endpoint handlers
│   ├── core/
│   │   ├── database.py             # SQLAlchemy engine and session factory
│   │   ├── logging_config.py       # Structured logging setup
│   │   ├── rate_limiter.py         # Token-bucket rate limiting middleware
│   │   ├── redis_client.py         # Async Redis client wrapper
│   │   ├── response.py             # Standardized success/error response helpers
│   │   ├── security.py             # PrivacyEngine (hashing + Fernet encryption)
│   │   ├── supabase.py             # Supabase client factory
│   │   └── vault.py                # Two-Vault identity store abstraction
│   ├── integrations/
│   │   └── composio_client.py      # Composio tool router (Calendar, Slack, GitHub)
│   ├── middleware/
│   │   ├── request_id.py           # Attach X-Request-ID to every request
│   │   ├── security.py             # OWASP headers and input sanitization
│   │   └── tenant_context.py       # Extract tenant_id from JWT or header
│   ├── models/
│   │   ├── analytics.py            # Vault A: Events, RiskScore, GraphEdge, etc.
│   │   ├── identity.py             # Vault B: UserIdentity, AuditLog
│   │   ├── notification.py         # Notification, NotificationPreference, Template
│   │   ├── tenant.py               # Tenant, TenantMember
│   │   └── workflow.py             # UserIntegration, WorkflowTemplate, WorkflowExecution
│   ├── orchestrator/               # AI agent orchestration layer
│   ├── schemas/
│   │   ├── ai.py                   # AI endpoint request/response schemas
│   │   ├── auth.py                 # Auth endpoint schemas
│   │   ├── common.py               # Shared schemas
│   │   ├── engines.py              # Engine endpoint schemas
│   │   └── tenant.py               # Tenant schemas
│   ├── services/
│   │   ├── safety_valve.py         # Burnout detection engine
│   │   ├── talent_scout.py         # Network centrality engine
│   │   ├── culture_temp.py         # Team health engine
│   │   ├── llm.py                  # LiteLLM wrapper (Gemini, OpenAI)
│   │   ├── simulation.py           # Digital twin / persona generator
│   │   ├── sir_model.py            # SIR epidemic contagion forecasting
│   │   ├── context.py              # External context enrichment (PagerDuty, Jira)
│   │   ├── nudge_dispatcher.py     # Intervention nudge dispatch
│   │   ├── permission_service.py   # RBAC permission checks
│   │   ├── sso_service.py          # SSO provider registry
│   │   ├── slack.py                # Slack integration
│   │   ├── talent_scout.py         # Network analysis
│   │   ├── tool_augmented_llm.py   # LLM with tool calling
│   │   └── websocket_manager.py    # WebSocket connection registry
│   ├── config.py                   # Settings via pydantic-settings
│   └── main.py                     # FastAPI app factory and startup
├── alembic/                        # Alembic migration scripts
├── tests/                          # Pytest test suite
├── requirements.txt
├── pyproject.toml
└── Dockerfile
```

### Request Lifecycle

```
HTTP Request
    │
    ▼
RequestIDMiddleware       — assigns X-Request-ID
    │
    ▼
SecurityMiddleware        — OWASP headers, input sanitization
    │
    ▼
TenantContextMiddleware   — extracts tenant_id from JWT or X-Tenant-ID header
    │
    ▼
RateLimitMiddleware       — token-bucket per IP
    │
    ▼
CORSMiddleware
    │
    ▼
FastAPI Router            — auth dependency injects current user from Supabase JWT
    │
    ▼
Endpoint Handler          — calls services / engines
    │
    ▼
Service Layer             — business logic, RBAC checks
    │
    ▼
SQLAlchemy ORM / Redis
```

---

## API Endpoints Summary

All endpoints are prefixed with `/api/v1`. Authentication is required on all endpoints unless noted.

### System

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/` | None | Engine status |
| `GET` | `/health` | None | Health check (version) |
| `GET` | `/ready` | None | Readiness probe (DB + Redis) |

### Auth (`/auth`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/auth/register` | None | Create account and default tenant |
| `POST` | `/auth/login` | None | Sign in, returns JWT pair and tenant list |
| `POST` | `/auth/refresh` | None | Refresh access token |
| `POST` | `/auth/logout` | Required | Sign out |
| `POST` | `/auth/forgot-password` | None | Send password reset email |
| `POST` | `/auth/reset-password` | None | Set new password using reset token |
| `GET` | `/auth/me` | Required | Current user profile and tenants |
| `POST` | `/auth/switch-tenant` | Required | Switch active workspace |

### SSO (`/sso`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/sso/{provider}/login` | Initiate SSO flow (google, azure_ad, saml) |
| `GET` | `/sso/{provider}/callback` | OAuth/SAML callback handler |

### Engines (`/engines`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/engines/personas` | Optional | Create simulation persona with 30 days of synthetic data |
| `GET` | `/engines/users/{user_hash}/context` | Optional | Contextual explanation for a timestamp |
| `GET` | `/engines/users/{user_hash}/safety` | Optional | Safety Valve burnout analysis |
| `GET` | `/engines/users/{user_hash}/talent` | Optional | Talent Scout network analysis |
| `POST` | `/engines/teams/culture` | Optional | Culture Thermometer team analysis |
| `POST` | `/engines/teams/forecast` | Optional | SIR contagion forecast |
| `GET` | `/engines/users/{user_hash}/nudge` | Optional | Generate LLM nudge message |
| `POST` | `/engines/users/{user_hash}/nudge/dismiss` | Required | Dismiss active nudge |
| `POST` | `/engines/users/{user_hash}/nudge/schedule-break` | Required | Log break scheduling action |
| `GET` | `/engines/events` | Optional | Recent activity stream |
| `POST` | `/engines/events/inject` | Optional | Inject simulated event |
| `GET` | `/engines/users` | Optional | Paginated user list with risk scores |
| `GET` | `/engines/users/{user_hash}/history` | Optional | Risk score history (default 30 days) |
| `GET` | `/engines/network/global/talent` | Optional | Global talent network analysis |
| `GET` | `/engines/global/network` | Optional | Global network metrics |
| `GET` | `/engines/dashboard/summary` | Optional | Role-filtered dashboard summary |
| `POST` | `/engines/users/{user_hash}/seed-history` | Optional | Seed historical data (admin/demo) |

### AI (`/ai`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/ai/report/risk/{user_hash}` | Required | LLM narrative risk report for a user |
| `GET` | `/ai/report/team/{team_hash}` | Required | LLM team health narrative |
| `GET` | `/ai/narratives/team/{team_hash}` | Required | Alias for team report |
| `POST` | `/ai/copilot/agenda` | Required | Generate 1:1 talking points |
| `POST` | `/ai/query` | Required | Natural language query over employee data |
| `POST` | `/ai/chat` | Required | Role-aware AI chat (employee/manager/admin) |
| `POST` | `/ai/chat/stream` | Required | Streaming SSE version of chat |

### Me (`/me`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/me/` | Required | Own profile, risk score, audit trail |
| `PUT` | `/me/consent` | Required | Update consent settings |
| `POST` | `/me/pause-monitoring` | Required | Pause monitoring for N hours |
| `DELETE` | `/me/data` | Required | GDPR right-to-be-forgotten (delete all data) |
| `GET` | `/me/risk` | Required | Own current risk score |
| `GET` | `/me/history` | Required | Own risk history |

### Admin (`/admin`)

| Method | Path | Auth | Role |
|---|---|---|---|
| `GET` | `/admin/health` | Required | admin |
| `GET` | `/admin/audit-logs` | Required | admin |
| `GET` | `/admin/users` | Required | admin |
| `PUT` | `/admin/users/{user_hash}/role` | Required | admin |

### Notifications (`/notifications`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/notifications/` | Required | List notifications (filterable by unread) |
| `POST` | `/notifications/{id}/read` | Required | Mark notification as read |
| `POST` | `/notifications/read-all` | Required | Mark all notifications as read |
| `DELETE` | `/notifications/{id}` | Required | Delete a notification |
| `GET` | `/notifications/preferences` | Required | Get notification preferences |
| `PUT` | `/notifications/preferences` | Required | Update notification preferences |

### External Tools (`/tools`)

Requires `COMPOSIO_API_KEY` to be configured.

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/tools/status` | Required | Check which tools are connected |
| `POST` | `/tools/execute` | Required | Execute a tool action (calendar, slack, github) |
| `POST` | `/tools/calendar/analyze` | Required | Analyze calendar meeting load for burnout signals |
| `GET` | `/tools/calendar/events/{entity_id}` | Required | Fetch calendar events |
| `POST` | `/tools/slack/activity` | Required | Get Slack activity metrics |

### Analytics (`/analytics`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/analytics/team-energy-heatmap` | None | Daily risk aggregates for heatmap (default 30 days) |

### Tenants (`/tenants`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/tenants/` | Required | List user's tenants |
| `POST` | `/tenants/` | Required | Create new tenant |
| `GET` | `/tenants/{id}` | Required | Get tenant details |
| `PUT` | `/tenants/{id}` | Required | Update tenant settings |

### ROI (`/roi`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/roi/calculate` | Calculate estimated burnout cost based on team size and risk data |

### WebSocket

| Path | Description |
|---|---|
| `ws://.../ws/{user_hash}` | Real-time risk updates for a specific user |
| `ws://.../ws/admin/team` | Admin/team-level broadcast channel |

---

## Database Schema Overview

Sentinel uses two PostgreSQL schemas to enforce the Two-Vault privacy boundary.

### Schema: `analytics` (Vault A — no PII)

#### `events`

Raw behavioral events. The analytics engine operates exclusively on these hashes.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer (PK) | Auto-increment |
| `user_hash` | String(64) | SHA-256 HMAC of email |
| `tenant_id` | UUID | Multi-tenant isolation |
| `timestamp` | DateTime | Event time |
| `event_type` | String(50) | `commit`, `pr_review`, `slack_message`, `unblocked` |
| `target_user_hash` | String(64) | For directed graph edges (nullable) |
| `metadata` | JSON | Event-specific payload |

Indexes: `(user_hash, timestamp)`, `event_type`.

#### `risk_scores`

Latest Safety Valve output per user.

| Column | Type | Notes |
|---|---|---|
| `user_hash` | String(64) (PK) | |
| `tenant_id` | UUID | |
| `velocity` | Float | Sentiment velocity (higher = more erratic) |
| `risk_level` | String(20) | `LOW`, `ELEVATED`, `CRITICAL` |
| `confidence` | Float | Statistical confidence (0–1) |
| `thwarted_belongingness` | Float | Interpersonal theory metric (0–1) |
| `updated_at` | DateTime | |

#### `risk_history`

Time-series snapshots for trend charts.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer (PK) | |
| `user_hash` | String(64) | Indexed |
| `tenant_id` | UUID | |
| `risk_level` | String(20) | |
| `velocity` | Float | |
| `confidence` | Float | |
| `belongingness_score` | Float | |
| `timestamp` | DateTime | |

Index: `(user_hash, timestamp)`.

#### `graph_edges`

Social collaboration graph for the Culture Thermometer.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer (PK) | |
| `tenant_id` | UUID | |
| `source_hash` | String(64) | |
| `target_hash` | String(64) | |
| `weight` | Float | Interaction frequency |
| `last_interaction` | DateTime | |
| `edge_type` | String(20) | `mentorship`, `collaboration`, `blocking` |

#### `centrality_scores`

Talent Scout outputs per user.

| Column | Type | Notes |
|---|---|---|
| `user_hash` | String(64) (PK) | |
| `tenant_id` | UUID | |
| `betweenness` | Float | Bridges disconnected groups |
| `eigenvector` | Float | Connected to important people |
| `unblocking_count` | Integer | Times unblocked others |
| `knowledge_transfer_score` | Float | |
| `calculated_at` | DateTime | |

#### `skill_profiles`

Employee skill dimensions for radar chart visualization.

| Column | Type | Notes |
|---|---|---|
| `user_hash` | String(64) (PK) | |
| `tenant_id` | UUID | |
| `technical` | Float | 0–100 |
| `communication` | Float | 0–100 |
| `leadership` | Float | 0–100 |
| `collaboration` | Float | 0–100 |
| `adaptability` | Float | 0–100 |
| `creativity` | Float | 0–100 |
| `updated_at` | DateTime | |

---

### Schema: `identity` (Vault B — encrypted PII)

#### `users`

Encrypted identity records. Only accessed when a nudge must be delivered or for RBAC lookups.

| Column | Type | Notes |
|---|---|---|
| `user_hash` | String(64) (PK) | Links to Vault A |
| `tenant_id` | UUID | |
| `email_encrypted` | LargeBinary | Fernet-encrypted |
| `slack_id_encrypted` | LargeBinary | Nullable |
| `role` | String(20) | `employee`, `manager`, `admin` |
| `consent_share_with_manager` | Boolean | |
| `consent_share_anonymized` | Boolean | Default `true` |
| `monitoring_paused_until` | DateTime | Nullable |
| `manager_hash` | String(64) | Nullable |
| `created_at` | DateTime | |

#### `audit_logs`

Immutable access trail.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer (PK) | |
| `user_hash` | String(64) | |
| `action` | String(50) | `nudge_sent`, `data_deleted`, `nudge_dismissed`, etc. |
| `details` | JSON | |
| `timestamp` | DateTime | |

#### `tenants`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `name` | String(255) | |
| `slug` | String(100) | Unique |
| `plan` | String(50) | `free`, `pro`, `enterprise` |
| `status` | String(20) | `active`, `suspended` |
| `settings` | JSON | |

#### `tenant_members`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `tenant_id` | UUID | FK → tenants |
| `user_hash` | String(64) | FK → users |
| `role` | String(20) | `owner`, `admin`, `member` |
| `invited_by` | String(64) | |
| `joined_at` | DateTime | |

Unique constraint: `(tenant_id, user_hash)`.

#### `notifications`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `user_hash` | String(64) | FK → users |
| `tenant_id` | UUID | |
| `type` | String(50) | `auth`, `team`, `system`, `security`, `activity` |
| `title` | String(255) | |
| `message` | String(1000) | |
| `data` | JSON | |
| `priority` | String(20) | `low`, `normal`, `high`, `critical` |
| `read_at` | DateTime | Nullable |
| `created_at` | DateTime | |

Indexes: `(user_hash, read_at)`, `(user_hash, created_at)`.

#### `notification_preferences`

Per-channel, per-type preferences.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `user_hash` | String(64) | FK → users |
| `channel` | String(20) | `in_app`, `email`, `sms` |
| `notification_type` | String(50) | `auth`, `team`, etc. |
| `enabled` | Boolean | |

---

### Workflow Tables (public schema)

These support the Composio workflow automation feature.

#### `user_integrations`

OAuth connections per user.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer (PK) | |
| `user_hash` | String(64) | |
| `tenant_id` | UUID | |
| `integration_id` | String(100) | e.g., `googlecalendar` |
| `integration_name` | String(255) | |
| `account_id` | String(100) | |
| `account_identifier` | String(255) | Display identifier |
| `scopes` | JSON | |
| `provider` | String(50) | |
| `status` | String(20) | `active`, `error`, `revoked` |
| `connected_at` | DateTime | |
| `last_used_at` | DateTime | |
| `token_expires_at` | DateTime | |

Unique constraint: `(user_hash, tenant_id, integration_id, account_id)`.

#### `workflow_templates`

Pre-built and custom automation templates.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer (PK) | |
| `template_id` | String(50) | Unique slug |
| `name` | String(255) | |
| `description` | Text | |
| `category` | String(50) | |
| `prompt_template` | Text | LLM instruction template |
| `required_integrations` | JSON | List of required tool names |
| `optional_integrations` | JSON | |
| `parameters` | JSON | User-configurable parameters |
| `is_public` | Boolean | |
| `is_system` | Boolean | |
| `usage_count` | Integer | |

#### `workflow_executions`

Execution log for every workflow run.

| Column | Type | Notes |
|---|---|---|
| `execution_id` | String(50) | Unique |
| `workflow_id` | String(50) | |
| `execution_type` | String(20) | `template`, `custom`, `scheduled` |
| `template_id` | String(50) | If using a template |
| `user_hash` | String(64) | |
| `tenant_id` | UUID | |
| `user_message` | Text | The user's original instruction |
| `status` | String(20) | `running`, `completed`, `failed` |
| `started_at` / `completed_at` | Timestamps | |
| `execution_time_ms` | Integer | |
| `tools_used` | JSON | List of Composio tools invoked |
| `integrations_used` | JSON | |
| `result_summary` | Text | |
| `result_artifacts` | JSON | |
| `full_conversation` | JSON | Complete LLM turn history |
| `error_message` | Text | Nullable |

---

## Authentication and Security Model

### Authentication Flow

1. User registers or logs in via `POST /auth/register` or `POST /auth/login`.
2. Credentials are validated against **Supabase Auth**.
3. Supabase returns a JWT access token (15-minute lifetime) and refresh token (7-day lifetime).
4. The frontend sends the access token as `Authorization: Bearer <token>` on all subsequent requests.
5. The `get_current_user_identity` dependency validates the JWT against Supabase and loads the `UserIdentity` record from the local database.
6. The `X-Tenant-ID` header or the `tenant_id` claim in the JWT sets the active tenant context.

### Role-Based Access Control

Three roles are enforced at the endpoint and service layers:

| Role | Access scope |
|---|---|
| `employee` | Own data only; consent-gated manager sharing |
| `manager` | Own data + direct reports (consent-dependent) + team aggregates |
| `admin` | All data, system health, user management |

The `require_role` dependency factory (`app/api/deps/auth.py`) enforces role membership and raises `403` on failure.

The `PermissionService` (`app/services/permission_service.py`) provides fine-grained `can_view_user_data(accessor, target)` checks that respect both role and consent settings.

### Privacy and Encryption

- **Identity hashing**: `HMAC-SHA256(email.lower(), VAULT_SALT)` produces a 32-char hex `user_hash`. This is irreversible without the salt.
- **PII encryption**: All PII (email, Slack ID) is encrypted with **Fernet** (AES-128-CBC + HMAC-SHA256) using the `ENCRYPTION_KEY`. Stored in `identity.users.email_encrypted` as `LargeBinary`.
- **Two-Vault separation**: The `analytics` schema never holds plaintext email or Slack ID. The engines operate purely on hashes. The `identity` schema is accessed only for nudge delivery and RBAC lookups, and every access is logged in `audit_logs`.

### Security Middleware

The following middleware layers are applied to every request:

- `SecurityMiddleware` — OWASP security headers, basic input sanitization
- `RateLimitMiddleware` — token-bucket algorithm, per-IP
- `TenantContextMiddleware` — populates `request.state.tenant_id`
- `RequestIDMiddleware` — attaches `X-Request-ID` for distributed tracing
- HTTP middleware in `main.py` — `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Content-Security-Policy`, and HSTS (production only)

### SSO

SSO providers are registered at startup from environment variables. Three providers are supported:

- **Google OAuth 2.0** — optional domain allow-list
- **Azure AD** — single tenant or multi-tenant (use `AZURE_TENANT_ID=common`)
- **SAML 2.0** — bring your own IdP metadata

---

## Environment Variables

See the [root README](../README.md#environment-variables-reference) for the full variable reference.

The minimum required set to run the backend is:

```env
DATABASE_URL=postgresql://user:password@localhost:5432/sentinel
JWT_SECRET=<at least 32 random characters>
VAULT_SALT=<any random string>
ENCRYPTION_KEY=<Fernet base64 key>
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=<anon key>
SUPABASE_SERVICE_KEY=<service role key>
```

---

## Running the Application

### Development

```bash
# Navigate to backend directory
cd backend

# Activate virtual environment
source .venv/bin/activate   # macOS / Linux
.venv\Scripts\activate      # Windows

# Install dependencies
pip install -r requirements.txt

# Start with hot-reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Endpoints:
- API: `http://localhost:8000`
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- Health: `http://localhost:8000/health`
- Readiness: `http://localhost:8000/ready`

### Production

```bash
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

### Database Migrations

Tables are created automatically on startup via `Base.metadata.create_all(engine)`. For production schema changes, use Alembic:

```bash
# Run all pending migrations
alembic upgrade head

# Create a new migration after model changes
alembic revision --autogenerate -m "describe the change"
```

---

## Running Tests

```bash
cd backend

# Run the full test suite
pytest

# Run with coverage report
pytest --cov=app --cov-report=html

# Run a specific file
pytest tests/test_safety_valve.py -v

# Run tests matching a keyword
pytest -k "auth" -v
```

---

## Key Modules Explained

### `app/services/safety_valve.py`

The Safety Valve engine detects burnout by analyzing behavioral velocity, circadian disruption, and social isolation. It reads from `analytics.events`, writes to `analytics.risk_scores` and `analytics.risk_history`, and publishes risk updates over WebSocket. The `seed_risk_history` method populates 30 days of synthetic historical data for demo personas.

### `app/services/talent_scout.py`

Builds a directed interaction graph using NetworkX from `analytics.graph_edges`. Computes betweenness centrality (bridge-building) and eigenvector centrality (connected to important people). Writes results to `analytics.centrality_scores`. High betweenness + low traditional visibility = "hidden gem."

### `app/services/culture_temp.py`

Aggregates individual risk scores across a team to assess collective health. Calculates graph fragmentation, communication decay rate, and uses the SIR epidemic model (`sir_model.py`) to forecast how burnout risk might spread through the network.

### `app/services/llm.py`

Wraps LiteLLM to provide a provider-agnostic interface. Supports synchronous (`generate_insight`) and streaming (`generate_chat_response_stream`) response modes. Configured via `LLM_PROVIDER`, `LLM_MODEL`, and the corresponding API key.

### `app/integrations/composio_client.py`

The Composio tool router client. Provides typed async methods for:
- `get_calendar_events` — fetches events via `GOOGLECALENDAR_LIST_EVENTS`
- `analyze_meeting_load` — detects high meeting density and back-to-back patterns
- `get_slack_activity` — message volume analysis via `SLACK_SEARCH_MESSAGES`
- `execute_tool` — generic action dispatcher for calendar, slack, and github

Only active when `COMPOSIO_API_KEY` is set.

### `app/core/redis_client.py`

Async Redis client wrapper using `redis.asyncio`. Provides `get`, `set` (with `nx` for atomic set-if-not-exists), `setex`, `delete`, `ttl`, and `ping`. Used for MCP session caching and distributed locking. A module-level singleton is exposed via `get_redis_client()`.

### `app/models/workflow.py`

Three SQLAlchemy models supporting the workflow automation feature: `UserIntegration` (per-user OAuth connections), `WorkflowTemplate` (pre-built and custom templates with required integrations and a prompt template), and `WorkflowExecution` (full execution audit trail including LLM turn history, tool usage, and timing breakdowns).

### `app/services/simulation.py`

The digital twin generator. `RealTimeSimulator` creates synthetic `Event` records with realistic patterns (late nights, weekend work, social isolation, etc.) that exercise all three engines. Used for demos and testing without real employee data.

### `app/middleware/tenant_context.py`

Extracts `tenant_id` from the JWT claims or the `X-Tenant-ID` request header and stores it in `request.state.tenant_id`. All subsequent queries scope to this tenant automatically.
