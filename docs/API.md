# Sentinel API Reference

**Last Updated:** 2026-04-03
**Base path:** `/api/v1`
**Auth:** All protected endpoints require `Authorization: Bearer <supabase-jwt>`.
**Tenant:** Active tenant is resolved from the JWT claim or the `X-Tenant-ID` request header.

---

## System

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/` | None | Engine status and version |
| `GET` | `/health` | None | Health check (version, environment) |
| `GET` | `/ready` | None | Readiness probe — checks DB and Redis connectivity |

---

## Auth (`/auth`)

Open registration is disabled. Users must be invited by an admin.

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/auth/login` | None | Sign in with email + password; returns JWT pair and tenant list |
| `POST` | `/auth/accept-invite` | None | Complete onboarding from an invitation token; sets password |
| `POST` | `/auth/refresh` | None | Exchange refresh token for a new access token |
| `POST` | `/auth/logout` | Required | Revoke the current session |
| `POST` | `/auth/forgot-password` | None | Send a password reset email |
| `POST` | `/auth/reset-password` | None | Set a new password using a reset token |
| `GET` | `/auth/me` | Required | Current user profile, roles, and tenant list |
| `POST` | `/auth/switch-tenant` | Required | Switch the active workspace |

---

## SSO (`/sso`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/sso/{provider}/login` | Initiate SSO flow (`google`, `azure_ad`, `saml`) |
| `GET` | `/sso/{provider}/callback` | OAuth / SAML callback handler |

---

## Engines (`/engines`)

Analytics and detection engines. Most endpoints accept `Optional` auth — callers without a valid JWT receive limited or demo-mode data.

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/engines/users` | Optional | Paginated user list with risk scores (tenant-scoped) |
| `GET` | `/engines/users/{user_hash}/safety` | Optional | Safety Valve burnout analysis for a user |
| `GET` | `/engines/users/{user_hash}/talent` | Optional | Talent Scout network centrality metrics |
| `GET` | `/engines/users/{user_hash}/context` | Optional | Contextual explanation for a risk timestamp |
| `GET` | `/engines/users/{user_hash}/history` | Optional | Risk score history (default 30 days) |
| `GET` | `/engines/users/{user_hash}/nudge` | Optional | Current LLM-generated wellbeing nudge |
| `POST` | `/engines/users/{user_hash}/nudge/dismiss` | Required | Dismiss the active nudge |
| `POST` | `/engines/users/{user_hash}/nudge/schedule-break` | Required | Log a break-scheduling action |
| `POST` | `/engines/users/{user_hash}/seed-history` | Optional | Seed synthetic risk history (admin/demo) |
| `POST` | `/engines/teams/culture` | Optional | Culture Thermometer team health analysis |
| `POST` | `/engines/teams/forecast` | Optional | SIR contagion forecast for a team |
| `POST` | `/engines/personas` | Optional | Create a simulation persona with 30 days of synthetic data |
| `GET` | `/engines/events` | Optional | Recent activity stream (all tenants, demo) |
| `POST` | `/engines/events/inject` | Optional | Inject a simulated event |
| `GET` | `/engines/network/global/talent` | Optional | Global talent network nodes and edges |
| `GET` | `/engines/global/network` | Optional | Global network metrics summary |
| `GET` | `/engines/dashboard/summary` | Optional | Role-filtered dashboard summary card data |

---

## AI (`/ai`)

All AI endpoints require authentication.

### Reports and Copilot

| Method | Path | Role | Description |
|---|---|---|---|
| `GET` | `/ai/report/risk/{user_hash}` | Any | LLM narrative risk report for a user (RBAC-gated by role) |
| `GET` | `/ai/report/team/{team_hash}` | manager, admin | LLM team health narrative |
| `GET` | `/ai/narratives/team/{team_hash}` | manager, admin | Alias for `/ai/report/team/{team_hash}` |
| `POST` | `/ai/copilot/agenda` | manager, admin | Generate 1:1 talking points for a direct report |
| `POST` | `/ai/query` | Any | Natural language query over tenant employee data |

### Chat

| Method | Path | Description |
|---|---|---|
| `POST` | `/ai/chat` | Role-aware chat (non-streaming). Auto-creates a session if no `session_id` is provided. Returns `conversation_id` (= session UUID). |
| `POST` | `/ai/chat/stream` | SSE streaming chat. Auto-creates a session. The `done` event includes `session_id`. |
| `POST` | `/ai/feedback` | Submit thumbs feedback. Body: `{ conversation_id, message_index, rating: "positive"\|"negative" }` |

### Chat History (legacy)

| Method | Path | Description |
|---|---|---|
| `GET` | `/ai/chat/history` | List recent conversations (`?limit=N`, max 50) |
| `GET` | `/ai/chat/history/{conversation_id}` | All turns for a conversation |

### Chat Sessions (CRUD)

| Method | Path | Description |
|---|---|---|
| `POST` | `/ai/chat/sessions` | Create a named session. Body: `{ title?: string }`. Returns `{ id, title, created_at }`. |
| `GET` | `/ai/chat/sessions` | Paginated session list. Query: `limit` (1–50), `offset`, `search` (title substring). |
| `GET` | `/ai/chat/sessions/{session_id}` | Session metadata + full message history. |
| `PUT` | `/ai/chat/sessions/{session_id}` | Rename session. Body: `{ title: string }`. |
| `DELETE` | `/ai/chat/sessions/{session_id}` | Soft-delete (preserves message history for audit). |
| `POST` | `/ai/chat/sessions/{session_id}/favorite` | Toggle the favorite/pin flag. Returns `{ id, is_favorite }`. |

**Auto-title:** When the chat or stream endpoints create a new session, they set its title to `"Untitled Chat"`. After the first assistant response is persisted, the LLM generates a short descriptive title (≤ 60 chars) from the user's first message, which is written back to the session.

---

## Me (`/me`)

Employee self-service. All endpoints require the caller to be the target user (RBAC enforced).

| Method | Path | Description |
|---|---|---|
| `GET` | `/me/` | Own profile, risk score, and audit trail |
| `PUT` | `/me/consent` | Update consent settings (share with manager, share anonymized) |
| `POST` | `/me/pause-monitoring` | Pause monitoring for N hours |
| `DELETE` | `/me/data` | GDPR right-to-be-forgotten: delete all personal data |
| `GET` | `/me/risk` | Own current risk score |
| `GET` | `/me/history` | Own risk score history |

---

## Admin (`/admin`)

All admin endpoints require role `admin`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/admin/health` | System health: DB stats, user counts, risk distribution |
| `GET` | `/admin/audit-logs` | Paginated audit log (`?limit`, `?offset`, `?action`) |
| `GET` | `/admin/users` | All users in the tenant with roles |
| `PUT` | `/admin/users/{user_hash}/role` | Change a user's role |
| `POST` | `/admin/invite` | Create and email an invitation link |
| `GET` | `/admin/pipeline/health` | Ask Sentinel pipeline component status (LLM, Redis, DB) |

---

## Users (`/users`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/users` | Required | Search user directory (`?query`, `?limit`) |
| `GET` | `/users/{user_hash}` | Required | User detail (RBAC-gated) |

---

## Teams (`/admin/teams`)

Team management is exposed under the admin prefix. Requires manager or admin role.

| Method | Path | Role | Description |
|---|---|---|---|
| `GET` | `/admin/teams` | manager, admin | List all teams in the tenant |
| `POST` | `/admin/teams` | admin | Create a team |
| `PUT` | `/admin/teams/{team_id}` | admin | Update team name or manager |
| `DELETE` | `/admin/teams/{team_id}` | admin | Delete a team |
| `POST` | `/admin/teams/{team_id}/members` | admin | Add a member to a team |
| `DELETE` | `/admin/teams/{team_id}/members/{user_hash}` | admin | Remove a member from a team |

---

## Notifications (`/notifications`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/notifications/` | List notifications (filter by `?unread=true`) |
| `POST` | `/notifications/{id}/read` | Mark one notification as read |
| `POST` | `/notifications/read-all` | Mark all notifications as read |
| `DELETE` | `/notifications/{id}` | Delete a notification |
| `GET` | `/notifications/preferences` | Get notification preferences (per channel + type) |
| `PUT` | `/notifications/preferences` | Update notification preferences |

---

## External Tools (`/tools`)

Requires `COMPOSIO_API_KEY` to be configured in the backend environment.

| Method | Path | Description |
|---|---|---|
| `GET` | `/tools/status` | Check which Composio tools are connected |
| `POST` | `/tools/execute` | Execute a Composio tool action (calendar, slack, github) |
| `POST` | `/tools/calendar/analyze` | Analyze calendar meeting load for burnout signals |
| `GET` | `/tools/calendar/events/{entity_id}` | Fetch calendar events for an entity |
| `POST` | `/tools/slack/activity` | Get Slack activity metrics |

---

## Ingestion (`/ingestion`)

| Method | Path | Role | Description |
|---|---|---|---|
| `POST` | `/ingestion/upload-csv` | admin | Bulk import employee behavioral data from CSV |
| `GET` | `/ingestion/status` | admin | Status of the most recent ingestion job |

---

## Analytics (`/analytics`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/analytics/team-energy-heatmap` | None | Daily risk aggregates for heatmap (default 30 days) |

---

## ROI (`/roi`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/roi/calculate` | Estimate burnout cost given team size and current risk distribution |

---

## Tenants (`/tenants`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/tenants/` | List workspaces the caller belongs to |
| `POST` | `/tenants/` | Create a new tenant |
| `GET` | `/tenants/{id}` | Get tenant details |
| `PUT` | `/tenants/{id}` | Update tenant settings |

---

## WebSocket

| Path | Description |
|---|---|
| `ws://.../ws/{user_hash}` | Real-time risk updates for a specific user |
| `ws://.../ws/admin/team` | Admin/team-level broadcast channel |

Incoming message types: `risk_update`, `manual_refresh`, `pong`.

---

## Standard Response Envelope

All REST endpoints return a consistent JSON structure:

```json
{
  "success": true,
  "data": { ... },
  "error": null
}
```

Error responses:

```json
{
  "success": false,
  "data": null,
  "error": "Human-readable error message"
}
```

HTTP status codes follow REST conventions: `200 OK`, `201 Created`, `400 Bad Request`, `401 Unauthorized`, `403 Forbidden`, `404 Not Found`, `422 Unprocessable Entity`, `500 Internal Server Error`.

---

## SSE Event Format (chat stream)

`POST /ai/chat/stream` returns `text/event-stream`. Each line is prefixed `data: `.

```json
{ "type": "token",  "content": "token text" }
{ "type": "done",   "conversation_id": "<uuid>", "session_id": "<uuid>", "done": true }
{ "type": "error",  "message": "error description" }
```

The client reads `session_id` from the `done` event to set `?conv=<session_id>` in the URL.
