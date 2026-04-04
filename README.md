# Sentinel Backend

AI-powered employee wellbeing platform — FastAPI backend with a 3-agent orchestrator, Composio MCP tool router, and three analytical engines (Safety Valve, Talent Scout, Culture Thermometer).

Auto-docs are available at `http://localhost:8000/docs` once the server is running.

---

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager: `pip install uv`
- A [Supabase](https://supabase.com) project (free tier works)
- Redis 7+ (optional — falls back to in-memory cache)

### 1. Install dependencies

```bash
cd backend
uv sync
```

### 2. Configure environment

Copy the template below into `backend/.env` and fill in the required values:

```dotenv
# ── Required ──────────────────────────────────────────────────────────────────

# PostgreSQL — use the "Session mode" connection string from Supabase > Settings > Database
DATABASE_URL=postgresql://postgres:[password]@db.[project-ref].supabase.co:5432/postgres

# Supabase — Project URL and keys from Supabase > Settings > API
SUPABASE_URL=https://[project-ref].supabase.co
SUPABASE_KEY=eyJ...          # anon / public key
SUPABASE_SERVICE_KEY=eyJ...  # service_role key (keep secret)

# JWT — must be 32+ characters; generate with:
#   python -c "import secrets; print(secrets.token_hex(32))"
JWT_SECRET=

# Privacy — salt for HMAC-SHA256 identity hashing (any secure string)
VAULT_SALT=

# Encryption — 44-char base64 Fernet key; generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=

# LLM — Google Gemini API key from https://aistudio.google.com/app/apikey
GEMINI_API_KEY=AIza...

# ── Optional ──────────────────────────────────────────────────────────────────

# COMPOSIO_API_KEY=          # Enables Slack/Calendar/GitHub tool integrations
# REDIS_URL=redis://localhost:6379/0
# PORTKEY_API_KEY=           # Portkey AI gateway proxy
# PORTKEY_VIRTUAL_KEY=       # Primary model virtual key (Portkey dashboard)
# LLM_MODEL=gemini-2.0-flash
# ENVIRONMENT=development    # Set to "production" to enable HSTS
# SIMULATION_MODE=True       # Keep True for demo mode
# SEED_PASSWORD=Demo123!     # Password assigned to seeded demo users
# ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3001
# LOG_LEVEL=INFO
```

### 3. Start the server

```bash
cd backend
uvicorn app.main:app --reload
```

The server starts on `http://localhost:8000`. Database tables are created automatically on first startup via `Base.metadata.create_all()`.

Verify it is running:

```bash
curl http://localhost:8000/health
# {"status":"healthy","version":"1.0.0"}
```

### 4. Seed demo data

```bash
cd backend
python -m scripts.seed_fresh
```

This wipes existing data and creates a complete demo environment:

- 1 tenant: **Acme Technologies**
- 13 users across 3 teams (Engineering, Design, Data Science)
- Pre-computed risk scores, skill profiles, and centrality scores
- ~550 behavioral events spread over 90 days
- Chat sessions, audit logs, and notification preferences

All demo users share the password `Demo123!`.

---

## Environment Variables

### Required

| Variable | Description | How to get it |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string | Supabase > Settings > Database > Session mode URI |
| `SUPABASE_URL` | Supabase project URL | Supabase > Settings > API |
| `SUPABASE_KEY` | Supabase anon key | Supabase > Settings > API |
| `SUPABASE_SERVICE_KEY` | Supabase service role key | Supabase > Settings > API |
| `JWT_SECRET` | JWT signing secret (32+ chars) | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `VAULT_SALT` | HMAC salt for privacy hashing | Any secure random string |
| `ENCRYPTION_KEY` | Fernet key for PII encryption | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `GEMINI_API_KEY` | Google Gemini API key | [aistudio.google.com](https://aistudio.google.com/app/apikey) |

### Optional

| Variable | Default | Description |
|---|---|---|
| `COMPOSIO_API_KEY` | `""` | Enables Slack, Calendar, GitHub, Gmail tool integrations |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis for MCP session cache; falls back to in-memory |
| `PORTKEY_API_KEY` | `""` | Portkey AI gateway proxy for LLM routing and observability |
| `PORTKEY_VIRTUAL_KEY` | `""` | Primary model virtual key (configure in Portkey dashboard) |
| `LLM_MODEL` | `gemini-2.0-flash` | Primary LLM model name |
| `SIMULATION_MODE` | `True` | Enables demo seed endpoints |
| `ENVIRONMENT` | `development` | Set `production` to enable HSTS headers |
| `SEED_PASSWORD` | `""` | Password used when seeding demo users |
| `ALLOWED_ORIGINS` | `http://localhost:3000,...` | CORS allowed origins (comma-separated) |
| `LOG_LEVEL` | `INFO` | Logging level |
| `MCP_SESSION_TTL_SECONDS` | `1800` | MCP session cache TTL in seconds |

---

## Architecture

### Three Engines

| Engine | What it detects | Math used |
|---|---|---|
| **Safety Valve** | Individual burnout risk | NumPy/SciPy `linregress`, Shannon entropy |
| **Talent Scout** | Structurally critical "hidden gems" | NetworkX betweenness + eigenvector centrality |
| **Culture Thermometer** | Team-level burnout spread | SciPy `odeint` (SIR epidemiological model) |

### 3-Agent Orchestrator

The `/api/v1/ai/chat` endpoint routes each user message through three layers:

1. **Intent Classifier** — Gemini via OpenAI-compatible endpoint; classifies intent as `org`, `task`, or `general` (temperature 0.1, JSON mode)
2. **Org Agent** — Answers questions about people, teams, and risk data using database context
3. **Task Agent** — Executes external tool calls (Composio: Slack, Calendar, GitHub, Gmail) via MCP

### Two-Vault Privacy Architecture

Analytics data (Vault A) and identity data (Vault B) are stored in separate schemas with no foreign key relationship. The link between them is an `HMAC-SHA256(email, VAULT_SALT)` hash. A database breach yields only anonymous hashes in Vault A and AES-128-CBC encrypted blobs in Vault B.

### Key Directories

```
app/
  api/v1/endpoints/    25 endpoint modules
  services/            27 service files (engines, agents, auth, notifications)
  models/              9 SQLAlchemy model files (identity, analytics, tenant, team...)
  core/                database.py, security.py, redis_client.py, rate_limiter.py
  middleware/          5 layers (security, rate limit, tenant context, request ID, CORS)
  integrations/        composio_client.py
```

---

## API Endpoints

All endpoints are prefixed with `/api/v1`. Full interactive docs at `http://localhost:8000/docs`.

| Domain | Prefix | What it does |
|---|---|---|
| Auth | `/auth` | Login, logout, refresh, password reset, email verify |
| SSO | `/sso` | Google, Azure AD, SAML flows |
| Me | `/me` | Current user profile, risk, skills, nudges |
| Team | `/team` | Team roster, aggregated risk, SIR curve |
| Engines | `/engines` | Safety Valve, Talent Scout, Culture Thermometer data |
| AI / Chat | `/ai` | Streaming chat, session CRUD, intent routing |
| Ingestion | `/ingestion` | Event ingest (behavioral metadata) |
| Admin | `/admin` | User management, RBAC, identity reveal |
| Organizations | `/organizations` | Org-level settings and members |
| Tenants | `/tenants` | Tenant provisioning |
| Users | `/users` | User directory |
| Notifications | `/notifications` | In-app notifications and preferences |
| Analytics | `/analytics` | Event aggregates and trend data |
| Tools | `/tools` | Composio external tool execution |
| ROI | `/roi` | Retention cost modelling |
| Demo | `/demo` | Demo reset and scenario controls |
| Connections | `/connections` | Graph edge data |
| Workflows | `/workflows` | Async workflow status |

WebSocket endpoint: `ws://localhost:8000/ws/{user_hash}` (real-time risk updates).

---

## Demo Credentials

All users belong to the **Acme Technologies** tenant. Password for all accounts: `Demo123!`

| Email | Name | Role | Team | Risk |
|---|---|---|---|---|
| `admin@acme.com` | Sarah Chen | Admin | — | LOW |
| `cto@acme.com` | James Wilson | Admin | — | LOW |
| `eng.manager@acme.com` | Priya Sharma | Manager | Engineering | ELEVATED |
| `design.manager@acme.com` | Alex Rivera | Manager | Design | LOW |
| `data.lead@acme.com` | Chen Wei | Manager | Data Science | LOW |
| `dev1@acme.com` | Jordan Lee | Employee | Engineering | CRITICAL |
| `dev2@acme.com` | Maria Santos | Employee | Engineering | LOW |
| `dev3@acme.com` | David Kim | Employee | Engineering | ELEVATED |
| `dev4@acme.com` | Emma Thompson | Employee | Engineering | LOW |
| `designer1@acme.com` | Noah Patel | Employee | Design | LOW |
| `designer2@acme.com` | Olivia Zhang | Employee | Design | ELEVATED |
| `analyst1@acme.com` | Liam Carter | Employee | Data Science | LOW |
| `analyst2@acme.com` | Sofia Martinez | Employee | Data Science | LOW |

`dev1@acme.com` (Jordan Lee) is the primary demo subject for burnout — CRITICAL risk, velocity 3.2, low communication and collaboration scores.

`dev4@acme.com` (Emma Thompson) is the hidden gem — highest betweenness centrality (0.85) and unblocking count (22), invisible to traditional performance metrics.

---

## Testing

```bash
cd backend
pytest
```

Run with coverage:

```bash
pytest --cov=app --cov-report=term-missing
```

The test suite has 35 test files covering auth dependencies, RBAC/permissions, middleware, tenant and team models, orchestrator intent classification, and identity reveal.

---

## Common Issues

**`ValueError: JWT_SECRET must be at least 32 characters`**
Generate a valid secret: `python -c "import secrets; print(secrets.token_hex(32))"`

**Database connection pool exhaustion under load**
The pool is configured conservatively (`pool_size=3`, `max_overflow=5`) to stay within Supabase free tier limits. Increase these values in `app/core/database.py` if you are on a paid plan.

**`aiohttp` version conflict on install**
Ensure you are using `uv sync` inside a fresh virtual environment: `uv venv && uv sync`.

**Composio tool calls return errors**
`COMPOSIO_API_KEY` must be set and the integrations must be connected at [app.composio.dev](https://app.composio.dev). Without the key, tool calls are disabled and the chat agent falls back to general responses.

**`ValueError: Supabase URL and Key must be configured`**
Both `SUPABASE_URL` and `SUPABASE_KEY` must be set in `.env`. The Supabase client is lazy-initialized — the error surfaces on the first request that needs it, not at startup.

---

## Production

```bash
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

Set `ENVIRONMENT=production` in your environment to enable HSTS headers. Use Alembic for schema migrations rather than relying on `create_all`:

```bash
alembic upgrade head
```
