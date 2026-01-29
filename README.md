# 🛡️ Sentinel Backend

> The core intelligence hub for the Sentinel platform, implementing the "Three Engines" architecture with privacy-first principles.

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.11+
- PostgreSQL (or Supabase)
- Docker (optional)

### 2. Infrastructure Setup
**Option A: Docker (Recommended)**
```bash
docker-compose up --build
```

**Option B: Local Dev**
```bash
# Create/Activate venv
python -m venv venv
.\venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration
Copy the `.env.example` to `.env` and fill in your Supabase credentials:
```bash
cp .env.example .env
```

**Critical Variables:**
| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL Connection String | `postgresql://...` |
| `ENCRYPTION_KEY` | Fernet 32-byte key | (Must generate) |
| `LLM_API_KEY` | Key for LLM provider (Gemini/Grok) | - |

### 4. Run Server
```bash
# Starts API on http://localhost:8000
uvicorn app.main:app --reload
```

---

## 🏗️ Architecture

### The Three Engines
1. **Safety Valve**: Burnout detection via Sentiment Velocity and Circadian Entropy.
2. **Talent Scout**: Network analysis to find "Hidden Gems" using centrality metrics.
3. **Culture Thermometer**: Team-level contagion analysis for retention risk.

### Privacy-First "Two Vault" System
- **Vault A (Analytics)**: Stores completely anonymized behavioral hashes and events.
- **Vault B (Identity)**: Stores encrypted identity mapping. Only accessible for high-priority "nudges".
- **Zero-Knowledge**: Analytics engine never sees PII.

### LLM Integration
Powered by **LiteLLM**, allowing model flexibility:
- **Provider Agnostic**: Switch between Gemini Pro, Grok, or GPT-4 via config.
- **Insight Generation**: Enhances quantitative analysis with qualitative explanations.

---

## 📚 API Reference

### Main Endpoints
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | System health check |
| `POST` | `/api/v1/personas` | Create simulated employee data |
| `POST` | `/api/v1/events` | Inject realtime event for demos |

### Engine Endpoints
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/users/{hash}/safety` | Get Safety Valve analysis |
| `GET` | `/api/v1/users/{hash}/talent` | Get Talent Scout analysis |
| `POST` | `/api/v1/teams/culture` | Get Culture Thermometer analysis |

Visit `http://localhost:8000/docs` for interactive Swagger documentation.

---

## 🛠️ Development

### Project Structure
```
backend/
├── app/
│   ├── api/          # Endpoints & Dependencies
│   ├── core/         # Config, Security, Database
│   ├── models/       # SQLAlchemy ORM Models
│   ├── services/     # Engine Logic (The "Brains")
│   └── schemas/      # Pydantic Response Models
├── tests/            # Pytest suite
└── .env              # Secrets (git-ignored)
```

### Running Tests
(Coming in Phase 5)

---

## 📜 License
Proprietary - Internal Use Only
