# Sentinel Backend

AI-powered employee insight engine. Python 3.12 + FastAPI + Supabase PostgreSQL + Redis backend implementing the Three Engines architecture with privacy-by-design principles and full RBAC.

---

## Table of Contents

- [Backend Architecture Overview](#backend-architecture-overview)
- [API Endpoints Summary](#api-endpoints-summary)
- [Database Schema Overview](#database-schema-overview)
- [Authentication and Security Model](#authentication-and-security-model)
- [RBAC — Role-Based Access Control](#rbac--role-based-access-control)
- [Ask Sentinel Chat Service](#ask-sentinel-chat-service)
- [Audit Service](#audit-service)
- [Environment Variables](#environment-variables)
- [Running the Application](#running-the-application)
- [Seed Demo Data](#seed-demo-data)
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
│   │   │   │   ├── admin.py        # Invite, user mgmt, pipeline health, audit logs
│   │   │   │   ├── ai.py           # Ask Sentinel chat, chat history, narrative reports
│   │   │   │   ├── analytics.py    # Team energy heatmap
│   │   │   │   ├── auth.py         # Login, accept-invite, refresh, logout
│   │   │   │   ├── auth_enhanced.py# MFA, passkeys, session management
│   │   │   │   ├── demo.py         # Demo scenarios and seeding
│   │   │   │   ├── engines.py      # Safety Valve, Talent Scout, Culture Therm.
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
│   │   │   └── auth.py             # get_current_user, get_tenant_member, require_role
│   │   └── websocket.py            # WebSocket endpoint handlers
│   ├── core/
│   │   ├── database.py             # Sync SQLAlchemy engine and session factory
│   │   ├── logging_config.py       # Structured logging setup
│   │   ├── rate_limiter.py         # Token-bucket rate limiting middleware
│   │   ├── redis_client.py         # Redis client wrapper
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
│   │   ├── identity.py             # Vault B: UserIdentity, AuditLog, ChatHistory
│   │   ├── notification.py         # Notification, NotificationPreference, Template
│   │   ├── team.py                 # Team (Phase 1: groups employees under a manager)
│   │   ├── tenant.py               # Tenant, TenantMember (canonical role + team_id)
│   │   └── workflow.py             # UserIntegration, WorkflowTemplate, WorkflowExecution
│   ├── orchestrator/               # AI agent orchestration layer
│   ├── schemas/
│   │   ├── ai.py                   # AI endpoint request/response schemas
│   │   ├── auth.py                 # Auth endpoint schemas (invite, accept-invite)
│   │   ├── common.py               # Shared schemas
│   │   ├── engines.py              # Engine endpoint schemas
│   │   └── tenant.py               # Tenant schemas
│   ├── services/
│   │   ├── safety_valve.py         # Burnout detection engine
│   │   ├── talent_scout.py         # Network centrality engine
│   │   ├── culture_temp.py         # Team health engine
│   │   ├── llm.py                  # LLM wrapper (Portkey / Gemini)
│   │   ├── sentinel_chat.py        # Ask Sentinel SSE pipeline (Phase 5)
│   │   ├── audit_service.py        # AuditService + AuditAction constants (Phase 6)
│   │   ├── simulation.py           # Digital twin / persona generator
│   │   ├── sir_model.py            # SIR epidemic contagion forecasting
│   │   ├── context.py              # External context enrichment (PagerDuty, Jira)
│   │   ├── nudge_dispatcher.py     # Intervention nudge dispatch
│   │   ├── permission_service.py   # PermissionService: 52 permissions (Phase 2)
│   │   ├── sso_service.py          # SSO provider registry
│   │   ├── slack.py                # Slack integration
│   │   ├── tool_augmented_llm.py   # LLM with tool calling
│   │   └── websocket_manager.py    # WebSocket connection registry
│   ├── config.py                   # Settings via pydantic-settings
│   └── main.py                     # FastAPI app factory and startup
├── scripts/
│   └── seed_fresh.py               # Demo seed: 13 users, 3 teams, Acme Corp
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

Open registration has been removed. New users must be invited by an admin.

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/auth/login` | None | Sign in, returns JWT pair and tenant list |
| `POST` | `/auth/accept-invite` | None | Complete onboarding from an invitation token |
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
| `POST` | `/ai/chat` | Required | Role-aware AI chat (employee/manager/admin); auto-creates session if no `session_id` provided |
| `POST` | `/ai/chat/stream` | Required | SSE streaming version of Ask Sentinel chat; auto-creates session |
| `POST` | `/ai/feedback` | Required | Submit thumbs-up/down feedback on a chat response |
| `GET` | `/ai/chat/history` | Required | List recent conversations (paginated) |
| `GET` | `/ai/chat/history/{conversation_id}` | Required | Full turn history for a conversation |
| `POST` | `/ai/chat/sessions` | Required | Create a new named chat session |
| `GET` | `/ai/chat/sessions` | Required | Paginated session list (supports `search`, `limit`, `offset`) |
| `GET` | `/ai/chat/sessions/{session_id}` | Required | Session metadata + full message history |
| `PUT` | `/ai/chat/sessions/{session_id}` | Required | Rename a session |
| `DELETE` | `/ai/chat/sessions/{session_id}` | Required | Soft-delete a session |
| `POST` | `/ai/chat/sessions/{session_id}/favorite` | Required | Toggle favorite/pin flag |

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

| Method | Path | Auth | Role | Description |
|---|---|---|---|---|
| `GET` | `/admin/health` | Required | admin | System health |
| `GET` | `/admin/audit-logs` | Required | admin | Paginated audit log |
| `GET` | `/admin/users` | Required | admin | All users with roles |
| `PUT` | `/admin/users/{user_hash}/role` | Required | admin | Change a user's role |
| `POST` | `/admin/invite` | Required | admin | Create and email an invitation |
| `GET` | `/admin/pipeline/health` | Required | admin | Ask Sentinel pipeline component status |

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

**Canonical role source.** A user's effective role within a tenant is always read from `tenant_members.role`, not `UserIdentity.role`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `tenant_id` | UUID | FK → tenants |
| `user_hash` | String(64) | FK → users |
| `role` | String(20) | `admin`, `manager`, `employee` |
| `team_id` | UUID | FK → teams (nullable) |
| `invited_by` | String(64) | user_hash of the inviting admin |
| `joined_at` | DateTime | |

Unique constraint: `(tenant_id, user_hash)`.

#### `teams`

Groups employees under a manager within a tenant (added in Phase 1).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `tenant_id` | UUID | FK → tenants (CASCADE) |
| `name` | String(100) | |
| `manager_hash` | String(64) | user_hash of the team manager (nullable) |
| `created_at` | DateTime | |

Unique constraint: `(tenant_id, name)`.

#### `chat_sessions` (within Vault B)

Named session containers for Ask Sentinel conversations. Each session groups many `chat_history` turns.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `user_hash` | String(64) | FK → users |
| `tenant_id` | UUID | |
| `title` | String(255) | Display name; auto-generated from first message |
| `is_favorite` | Boolean | Default `false`; toggled via favorite endpoint |
| `is_active` | Boolean | Default `true`; set to `false` on soft-delete |
| `created_at` | DateTime | |
| `updated_at` | DateTime | |

Index: `(user_hash, tenant_id, is_active, updated_at)`.

#### `chat_history` (within Vault B)

Persists Ask Sentinel conversation turns. Each row is a single message belonging to a `chat_sessions` record.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `conversation_id` | String | FK → `chat_sessions.id` |
| `user_hash` | String(64) | |
| `tenant_id` | UUID | |
| `role` | String | `user` or `assistant` |
| `type` | String | Message type tag (default `message`) |
| `content` | Text | Message body |
| `metadata_` | JSON | Arbitrary metadata (e.g., `{"role": "manager"}`) |
| `created_at` | DateTime | |

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

1. An admin invites a new user via `POST /admin/invite`. A time-limited invitation token is emailed.
2. The invitee accepts via `POST /auth/accept-invite`, sets a password, and is registered in Supabase Auth.
3. Credentials are validated against **Supabase Auth** on subsequent logins via `POST /auth/login`.
4. Supabase returns a JWT access token (15-minute lifetime) and refresh token (7-day lifetime).
5. The frontend sends the access token as `Authorization: Bearer <token>` on all subsequent requests.
6. The `get_current_user_identity` dependency validates the JWT against Supabase and loads the `UserIdentity` record from the local database.
7. `get_tenant_member` resolves the caller's `TenantMember` row — the single source of truth for `role` and `team_id`.
8. The `X-Tenant-ID` header or the `tenant_id` claim in the JWT sets the active tenant context.

### Role-Based Access Control

Three roles are enforced at the endpoint and service layers:

| Role | Access scope |
|---|---|
| `employee` | Own data only; consent-gated manager sharing |
| `manager` | Own data + direct reports (consent-dependent) + team aggregates |
| `admin` | All data, system health, user management, 36 h critical override |

The `require_role` dependency factory (`app/api/deps/auth.py`) enforces role membership and raises `403` on failure.

The `PermissionService` (`app/services/permission_service.py`) provides fine-grained `can_view_user_data(accessor, target)` checks that respect both role and consent settings. See the [RBAC section](#rbac--role-based-access-control) below for the full permission list.

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

## RBAC — Role-Based Access Control

Implemented in Phase 2. All role information is authoritative from `TenantMember.role`; `UserIdentity.role` is not used for access decisions.

### Dependency chain

```
get_current_user()          — validates Supabase JWT, returns UserIdentity
    └── get_tenant_member() — loads TenantMember row for active tenant
            └── require_role("admin") — raises 403 if role not satisfied
```

### 52 permissions (grouped)

| Group | Permissions |
|---|---|
| User data | `view_own_data`, `view_direct_report_data`, `view_all_user_data`, `view_team_data`, `view_aggregated_data` |
| Risk scores | `view_own_risk`, `view_report_risk`, `view_all_risk`, `export_risk_data` |
| Identity | `reveal_identity`, `critical_override` (36 h window, admin only) |
| Audit | `view_own_audit`, `view_all_audit`, `export_audit` |
| Consent / monitoring | `update_own_consent`, `manage_consent`, `pause_own_monitoring`, `manage_monitoring` |
| AI chat | `use_ai_chat`, `view_ai_insights`, `configure_ai` |
| Team management | `view_team_list`, `create_team`, `update_team`, `delete_team`, `assign_team_member` |
| User management | `invite_user`, `remove_user`, `change_user_role`, `view_user_directory` |
| Integrations | `connect_integration`, `disconnect_integration`, `view_integrations`, `execute_tool` |
| Reports | `view_reports`, `generate_report`, `export_report`, `schedule_report` |
| Notifications | `view_notifications`, `manage_notifications`, `configure_notifications` |
| System | `view_system_health`, `manage_system_settings`, `view_pipeline_health` |

### 36 h critical override

Admins may call `PermissionService.record_critical_override()` to log an `AuditAction.CRITICAL_OVERRIDE_ACCESS` event and temporarily elevate access to CRITICAL-risk employee identity data. The override window is 36 hours.

---

## Ask Sentinel Chat Service

`app/services/sentinel_chat.py` orchestrates every Ask Sentinel response through a typed SSE pipeline.

### Session management

Every call to `POST /ai/chat` or `POST /ai/chat/stream` automatically resolves or creates a `ChatSession`. If the request includes a `session_id` the existing session is resumed; if not, a new session is created with the title `"Untitled Chat"` and its UUID is returned in the response so the client can bookmark it.

After the first message in a new session, `ChatHistoryService.auto_title_session` calls the LLM to generate a short descriptive title (≤ 60 chars) from the user's opening message. The title is written back to `chat_sessions.title` before the next request.

### SSE pipeline

```
POST /ai/chat/stream (EventSourceResponse)
    │
    ▼  stage: "refusal"
RefusalClassifier          — blocks out-of-scope or harmful queries (emits refusal event)
    │
    ▼  stage: "workflow"
WorkflowIntentParser       — detects actionable intents (calendar block, Slack nudge, etc.)
    │
    ▼  stage: "boundary"
DataBoundaryEnforcer       — builds role-scoped context (employee / manager / admin)
    │
    ▼  stage: "tools" (optional)
Tool augmentation          — fetches calendar / Slack data if intent requires it
    │
    ▼  stage: "llm"
LLM call (Portkey/Gemini)  — streams tokens to the client
    │
    ▼  stage: "done"
Terminal event             — always emitted, signals stream end
```

SSE event envelope:

```json
{ "stage": "llm", "delta": "token text", "done": false }
{ "stage": "done", "conversation_id": "uuid", "done": true }
```

Chat turns are persisted to `identity.chat_history` so they are retrievable via `GET /ai/chat/history`, `GET /ai/chat/history/{conversation_id}`, and the session-based `GET /ai/chat/sessions/{session_id}` endpoint.

The frontend uses a native `fetch` + `ReadableStream` approach with an `AbortController` for cancellation (no Vercel AI SDK dependency on the backend).

---

## Audit Service

Implemented in Phase 6. `app/services/audit_service.py` is the single entry point for all audit writes.

```python
AuditService.log(db, user_hash, AuditAction.ROLE_CHANGED, details={...})
```

### `AuditAction` constants (18 total)

| Group | Constants |
|---|---|
| Identity / access | `IDENTITY_REVEALED`, `CRITICAL_OVERRIDE_ACCESS`, `DATA_ACCESSED`, `DATA_EXPORTED`, `OUT_OF_SCOPE_QUERY` |
| User lifecycle | `ROLE_CHANGED`, `USER_INVITED`, `USER_REMOVED`, `USER_DEACTIVATED` |
| Team management | `TEAM_MODIFIED` |
| Consent / monitoring | `CONSENT_CHANGED`, `MONITORING_PAUSED` |
| Workflows | `WORKFLOW_CREATED`, `WORKFLOW_EXECUTED` |
| Tools / integrations | `TOOL_CONNECTED`, `TOOL_DISCONNECTED` |
| Engine / data ops | `ENGINE_RECOMPUTED`, `CSV_UPLOADED` |

All writes are immutable rows in `identity.audit_logs`. The `GET /admin/audit-logs` endpoint exposes them to admins with filtering.

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

## Seed Demo Data

```bash
cd backend

# Windows
.venv\Scripts\python -m scripts.seed_fresh

# macOS / Linux
python -m scripts.seed_fresh

# Alternative (module path form, cross-platform)
python -m scripts.seed_fresh
```

Creates the **Acme Corp** tenant with 13 users across 3 teams (Engineering, Design, Data Science). All passwords are `Demo123!`. Key accounts:

| Email | Role | Notes |
|---|---|---|
| `admin@acme.com` | admin | Primary admin |
| `eng.manager@acme.com` | manager | Engineering lead |
| `dev1@acme.com` | employee | CRITICAL burnout risk |

See the [root README demo data section](../README.md#demo-data) for the full user list.

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

Provider-agnostic LLM interface with a three-tier fallback strategy:

1. **Portkey gateway** (if `PORTKEY_API_KEY` and a valid virtual key are set) — provides automatic retries and fallback routing.
2. **Gemini 2.5 Flash direct** (if `GEMINI_API_KEY` is set) — connects to Google's OpenAI-compatible endpoint. This is the default for the demo environment.
3. **Groq direct** (if `LLM_API_KEY` is set) — uses `llama-3.3-70b-versatile` via the Groq OpenAI-compatible endpoint.

Supports synchronous (`generate_insight`) and streaming (`generate_chat_response_stream`) response modes.

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

### `app/services/permission_service.py`

Implements `PermissionService` with 52 named permission checks grouped by domain. The `can_view_user_data(accessor_member, target_hash, db)` method is the primary call site for data-access decisions. It respects both `TenantMember.role` and the target user's consent settings. Admins with a valid critical override may bypass consent gates for CRITICAL-risk users within a 36-hour window.

### `app/services/chat_history_service.py`

`ChatHistoryService` is the single data-access layer for all chat persistence. Key methods:

- `create_session` — inserts a new `ChatSession` row.
- `get_session` / `get_sessions` — tenant-scoped session retrieval with optional title search.
- `rename_session` — updates `title` and `updated_at`.
- `delete_session` — soft-delete (`is_active=False`); underlying turns are preserved.
- `toggle_favorite` — flips `is_favorite`.
- `persist_turn` — inserts a `ChatHistory` row and flushes to the session.
- `get_conversation_turns` — returns all turns for a session ordered by `created_at`.
- `auto_title_session` — calls the LLM to generate a short descriptive title from the first user message, then writes it back to `ChatSession.title`.

All queries include `user_hash` and `tenant_id` as mandatory filters; cross-user and cross-tenant access is structurally prevented.

### `app/services/sentinel_chat.py`

Orchestrates Ask Sentinel responses. The `stream_chat_response(message, role, db, ...)` async generator yields SSE-formatted JSON strings. It runs the five-stage pipeline (refusal → workflow → boundary → LLM → done) and persists each turn to `identity.chat_history` via `ChatHistoryService`.

### `app/services/audit_service.py`

Provides `AuditService.log(db, user_hash, action, details)` — the only approved way to write to `identity.audit_logs`. Action constants live in `AuditAction` (18 snake_case strings). Using string constants rather than an Enum avoids `.value` boilerplate at every call site while keeping IDE autocomplete.

### `app/models/team.py`

Defines the `Team` model in the `identity` schema. Teams belong to a tenant and optionally have a `manager_hash`. `TenantMember` rows carry a `team_id` FK, making team membership a first-class attribute on every user's tenant record.
