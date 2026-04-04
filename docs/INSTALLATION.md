# Installation Guide

> Complete step-by-step instructions to set up the Calendar Management Agent from scratch on any machine.

---

## Table of Contents

1. [System Requirements](#system-requirements)
2. [Quick Install (5 minutes)](#quick-install-5-minutes)
3. [Detailed Install — macOS](#detailed-install--macos)
4. [Detailed Install — Ubuntu/Debian](#detailed-install--ubuntudebian)
5. [Detailed Install — Windows](#detailed-install--windows)
6. [Dependency Files Reference](#dependency-files-reference)
7. [External Services Setup](#external-services-setup)
8. [Environment Configuration](#environment-configuration)
9. [Verify Installation](#verify-installation)
10. [Docker Install (Alternative)](#docker-install-alternative)
11. [Troubleshooting](#troubleshooting)

---

## System Requirements

| Requirement | Minimum | Recommended |
|---|---|---|
| **Python** | 3.11 | 3.11.7 (via pyenv) |
| **OS** | macOS 12+, Ubuntu 20.04+, Windows 10+ | macOS 13+ / Ubuntu 22.04+ |
| **RAM** | 2 GB | 4 GB |
| **Disk** | 1 GB (for deps + venv) | 2 GB |
| **Redis** | 7.0+ | 7.2+ (optional for dev, required for prod) |
| **PostgreSQL** | 14+ | 16+ (optional — SQLite used in dev by default) |
| **Docker** | 24+ | 25+ (optional — alternative install method) |

### Required API Keys (at least one)

| Service | Required | How to Get |
|---|---|---|
| **Anthropic API Key** | Yes (if using Claude) | https://console.anthropic.com/settings/keys |
| **OpenAI API Key** | Yes (if using GPT) | https://platform.openai.com/api-keys |
| **Google OAuth Credentials** | For calendar access | https://console.cloud.google.com/apis/credentials |
| **Stripe API Keys** | For billing (optional) | https://dashboard.stripe.com/test/apikeys |

> **Note:** You only need ONE LLM API key (Anthropic OR OpenAI) to get started.

---

## Quick Install (5 minutes)

For developers who know what they're doing:

```bash
# 1. Clone and enter project
git clone <repository-url> CalendarManagementAI
cd CalendarManagementAI

# 2. Python 3.11+ (skip if already installed)
pyenv install 3.11.7 && pyenv local 3.11.7

# 3. Virtual environment + dependencies
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 4. Configure
cp .env.example .env
# Edit .env → set LLM_PROVIDER and API key

# 5. Database
alembic upgrade head

# 6. Run
make run     # → http://localhost:8000/docs

# 7. Test
make test-unit   # → 46 tests should pass
```

---

## Detailed Install — macOS

### Step 1: Install Homebrew (if not present)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### Step 2: Install Python 3.11 via pyenv

```bash
# Install pyenv
brew install pyenv

# Add to shell profile (~/.zshrc)
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.zshrc
echo 'command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.zshrc
echo 'eval "$(pyenv init -)"' >> ~/.zshrc
source ~/.zshrc

# Install Python 3.11.7
pyenv install 3.11.7

# Navigate to project and set local version
cd CalendarManagementAI
pyenv local 3.11.7

# Verify
python --version   # → Python 3.11.7
```

### Step 3: Create Virtual Environment

```bash
python -m venv .venv
source .venv/bin/activate

# Verify venv is active
which python   # → /path/to/CalendarManagementAI/.venv/bin/python
```

### Step 4: Install Dependencies

Choose one method:

```bash
# Option A: Editable install from pyproject.toml (RECOMMENDED for development)
pip install -e ".[dev]"

# Option B: Reproducible install from lock file (exact versions)
pip install -r requirements.lock
pip install -e .

# Option C: From requirements files
pip install -r requirements.txt -r requirements-dev.txt
pip install -e .
```

### Step 5: Install Redis (optional for development)

```bash
brew install redis
brew services start redis

# Verify
redis-cli ping   # → PONG
```

### Step 6: Install PostgreSQL (optional — SQLite is default for dev)

```bash
brew install postgresql@16
brew services start postgresql@16

# Create database
createdb calendar_agent
```

### Step 7: Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your values. **Minimum required:**

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your-actual-key-here
```

### Step 8: Run Database Migrations

```bash
alembic upgrade head
```

### Step 9: Start the Application

```bash
make run
# → Server starts at http://localhost:8000
# → Swagger UI at http://localhost:8000/docs
```

---

## Detailed Install — Ubuntu/Debian

### Step 1: System Dependencies

```bash
sudo apt update && sudo apt install -y \
    build-essential \
    libssl-dev \
    zlib1g-dev \
    libbz2-dev \
    libreadline-dev \
    libsqlite3-dev \
    libncursesw5-dev \
    xz-utils \
    tk-dev \
    libxml2-dev \
    libxmlsec1-dev \
    libffi-dev \
    liblzma-dev \
    git \
    curl
```

### Step 2: Install Python 3.11 via pyenv

```bash
# Install pyenv
curl https://pyenv.run | bash

# Add to ~/.bashrc
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.bashrc
echo 'command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.bashrc
echo 'eval "$(pyenv init -)"' >> ~/.bashrc
source ~/.bashrc

# Install Python
pyenv install 3.11.7
cd CalendarManagementAI
pyenv local 3.11.7
```

### Step 3: Virtual Environment + Dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

### Step 4: Redis

```bash
sudo apt install -y redis-server
sudo systemctl enable redis-server
sudo systemctl start redis-server
redis-cli ping   # → PONG
```

### Step 5: PostgreSQL (optional)

```bash
sudo apt install -y postgresql postgresql-contrib
sudo systemctl start postgresql

sudo -u postgres createuser --interactive  # create your user
sudo -u postgres createdb calendar_agent
```

### Steps 6-9: Same as macOS

```bash
cp .env.example .env
# Edit .env
alembic upgrade head
make run
```

---

## Detailed Install — Windows

### Step 1: Install Python 3.11

Download Python 3.11.x from https://www.python.org/downloads/

> **Important:** Check "Add Python to PATH" during installation.

Or use the Windows Store:
```powershell
winget install Python.Python.3.11
```

### Step 2: Clone and Setup

```powershell
git clone <repository-url> CalendarManagementAI
cd CalendarManagementAI

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"
```

### Step 3: Redis (Windows)

Option A — Docker Desktop:
```powershell
docker run -d --name redis -p 6379:6379 redis:7-alpine
```

Option B — Memurai (Redis-compatible for Windows):
Download from https://www.memurai.com/

### Step 4: Configure and Run

```powershell
copy .env.example .env
# Edit .env with notepad or your editor

alembic upgrade head

uvicorn src.api.rest.app:create_app --factory --host 0.0.0.0 --port 8000 --reload
```

---

## Dependency Files Reference

This project provides multiple dependency files for different use cases:

| File | Purpose | When to Use |
|---|---|---|
| `pyproject.toml` | Source of truth (PEP 621) | `pip install -e ".[dev]"` — development |
| `requirements.txt` | Direct production deps (flexible versions) | `pip install -r requirements.txt` |
| `requirements-dev.txt` | Dev-only deps (testing, linting) | `pip install -r requirements-dev.txt` |
| `requirements.lock` | **All** 116 packages pinned to exact versions | Reproducible builds in CI/CD |

### Relationship

```
pyproject.toml              ← Source of truth (defines version ranges)
    │
    ├── requirements.txt    ← Extracted production dependencies
    ├── requirements-dev.txt ← Extracted dev dependencies
    └── requirements.lock   ← Full resolution (pip freeze output)
```

### When to Regenerate requirements.lock

After any dependency change:

```bash
# Update a dependency
pip install "langchain>=0.3.26"

# Regenerate lock file
pip freeze | grep -v "^-e " > requirements.lock
```

### All 116 Pinned Packages (requirements.lock)

The lock file contains the exact versions tested and verified on **Python 3.11.7 / macOS** as of March 2026. Key packages:

**AI / LLM Stack:**
| Package | Version | Purpose |
|---|---|---|
| langchain | 0.3.25 | LLM framework |
| langgraph | 0.2.76 | Agent state machine |
| langchain-openai | 0.2.14 | OpenAI LangChain integration |
| langchain-anthropic | 0.3.15 | Anthropic LangChain integration |
| anthropic | 0.86.0 | Anthropic SDK |
| openai | 1.109.1 | OpenAI SDK |
| langsmith | 0.2.11 | LLM tracing |

**Web Framework:**
| Package | Version | Purpose |
|---|---|---|
| fastapi | 0.135.2 | API framework |
| uvicorn | 0.42.0 | ASGI server |
| starlette | 1.0.0 | ASGI toolkit |
| websockets | 13.1 | WebSocket support |

**Database:**
| Package | Version | Purpose |
|---|---|---|
| sqlalchemy | 2.0.48 | ORM (async) |
| alembic | 1.18.4 | Migrations |
| asyncpg | 0.31.0 | PostgreSQL driver |
| aiosqlite | 0.22.1 | SQLite async driver |

**Cache & Auth:**
| Package | Version | Purpose |
|---|---|---|
| redis | 5.3.1 | Redis client |
| python-jose | 3.5.0 | JWT tokens |
| google-auth | 2.49.1 | Google OAuth |
| passlib | 1.7.4 | Password hashing |

**Billing & Config:**
| Package | Version | Purpose |
|---|---|---|
| stripe | 9.12.0 | Payment processing |
| pydantic | 2.12.5 | Validation |
| pydantic-settings | 2.13.1 | Env config |

**Monitoring & Utils:**
| Package | Version | Purpose |
|---|---|---|
| sentry-sdk | 2.56.0 | Error tracking |
| structlog | 24.4.0 | Structured logging |
| httpx | 0.28.1 | HTTP client |
| cryptography | 43.0.3 | Encryption |
| tenacity | 9.1.4 | Retry logic |

**Dev Tools:**
| Package | Version | Purpose |
|---|---|---|
| pytest | 8.4.2 | Test framework |
| pytest-asyncio | 0.26.0 | Async test support |
| pytest-cov | 5.0.0 | Coverage |
| pytest-mock | 3.15.1 | Mocking |
| ruff | 0.15.8 | Linter + formatter |
| mypy | 1.19.1 | Type checker |
| pre-commit | 3.8.0 | Git hooks |
| faker | 25.9.2 | Test data |
| factory-boy | 3.3.3 | Test factories |

---

## External Services Setup

### Google Calendar OAuth (required for calendar features)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing
3. Enable **Google Calendar API**:
   - APIs & Services → Library → Search "Google Calendar API" → Enable
4. Create OAuth credentials:
   - APIs & Services → Credentials → Create Credentials → OAuth client ID
   - Application type: **Web application**
   - Authorized redirect URIs: `http://localhost:8000/api/v1/auth/google/callback`
5. Copy Client ID and Client Secret to `.env`:

```bash
GOOGLE_CLIENT_ID=123456789-xxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxx
GOOGLE_REDIRECT_URI=http://localhost:8000/api/v1/auth/google/callback
```

### Anthropic API Key

1. Go to [Anthropic Console](https://console.anthropic.com/)
2. Create an account and add billing
3. Settings → API Keys → Create Key
4. Copy to `.env`:

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxx
```

### OpenAI API Key (alternative to Anthropic)

1. Go to [OpenAI Platform](https://platform.openai.com/)
2. Create an account and add billing
3. API Keys → Create new secret key
4. Copy to `.env`:

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxx
```

### Stripe (optional — for billing features)

1. Go to [Stripe Dashboard](https://dashboard.stripe.com/)
2. Use **test mode** for development
3. Developers → API keys → Copy Secret key
4. Set up webhook endpoint (for production):
   - Developers → Webhooks → Add endpoint
   - URL: `https://your-domain.com/api/v1/billing/webhook`
   - Events: `customer.subscription.*`, `invoice.*`
5. Copy to `.env`:

```bash
STRIPE_SECRET_KEY=sk_test_xxxxxxxxxxxx
STRIPE_WEBHOOK_SECRET=whsec_xxxxxxxxxxxx
```

### LangSmith (optional — for LLM tracing)

1. Go to [LangSmith](https://smith.langchain.com/)
2. Settings → API Keys → Create
3. Copy to `.env`:

```bash
LANGSMITH_API_KEY=lsv2_pt_xxxxxxxxxxxx
LANGSMITH_PROJECT=calendar-agent
```

### Sentry (optional — for error tracking)

1. Go to [Sentry](https://sentry.io/)
2. Create a project (Python / FastAPI)
3. Copy DSN to `.env`:

```bash
SENTRY_DSN=https://xxxx@xxxx.ingest.sentry.io/xxxx
```

---

## Environment Configuration

### Minimum `.env` for Development

```bash
# Bare minimum to run the app locally
APP_ENV=development
APP_SECRET_KEY=dev-secret-key-change-in-production
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Everything else has sensible defaults:
- Database: SQLite (zero setup)
- Redis: Optional for dev (caching disabled gracefully)
- Stripe: Optional (billing features inactive)
- Google OAuth: Optional (calendar features inactive, placeholder responses)

### Full `.env` for Production

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the complete production environment reference.

---

## Verify Installation

Run these commands after setup to confirm everything works:

### 1. Python Version

```bash
python --version
# Expected: Python 3.11.x
```

### 2. Dependencies Installed

```bash
pip list | wc -l
# Expected: ~117 packages
```

### 3. Unit Tests

```bash
make test-unit
# Expected: 46 passed in ~0.2s
```

### 4. Application Boots

```bash
python -c "
from src.api.rest.app import create_app
app = create_app()
routes = [r.path for r in app.routes if hasattr(r, 'path')]
print(f'✅ App created — {len(routes)} routes')
"
# Expected: ✅ App created — 13 routes
```

### 5. LLM Factory Works

```bash
python -c "
from src.infrastructure.llm.factory import create_llm_adapter
a = create_llm_adapter('anthropic', 'test')
o = create_llm_adapter('openai', 'test')
print(f'✅ Anthropic: {type(a).__name__}')
print(f'✅ OpenAI: {type(o).__name__}')
"
# Expected:
# ✅ Anthropic: AnthropicAdapter
# ✅ OpenAI: OpenAIAdapter
```

### 6. Dev Server Starts

```bash
make run
# Expected: INFO:     Uvicorn running on http://0.0.0.0:8000
# Visit: http://localhost:8000/docs → Swagger UI loads
# Visit: http://localhost:8000/health → {"status":"healthy","service":"calendar-agent"}
```

---

## Docker Install (Alternative)

Skip all Python/pyenv setup — Docker handles everything:

### Prerequisites

- Docker 24+ and Docker Compose v2
- That's it.

### Steps

```bash
# 1. Clone
git clone <repository-url> CalendarManagementAI
cd CalendarManagementAI

# 2. Configure
cp .env.example .env
# Edit .env → set LLM_PROVIDER and API key

# 3. Build and start (app + PostgreSQL + Redis)
docker compose up -d --build

# 4. Run migrations
docker compose exec app alembic upgrade head

# 5. Verify
curl http://localhost:8000/health
# → {"status":"healthy","service":"calendar-agent"}

# 6. View logs
docker compose logs -f app
```

### Docker Services

| Service | Image | Port | Purpose |
|---|---|---|---|
| `app` | Built from Dockerfile | 8000 | Calendar Agent API |
| `postgres` | postgres:16-alpine | 5432 | Database |
| `redis` | redis:7-alpine | 6379 | Cache + usage tracking |

### Stop / Clean Up

```bash
docker compose down          # Stop services
docker compose down -v       # Stop + delete data volumes
```

---

## Troubleshooting

### Python version errors

```
ERROR: Requires Python >=3.11 but found 3.9.6
```

**Fix:** Install Python 3.11+ via pyenv:
```bash
pyenv install 3.11.7
pyenv local 3.11.7
python -m venv .venv    # Recreate venv with correct Python
source .venv/bin/activate
pip install -e ".[dev]"
```

### `ModuleNotFoundError: No module named 'src'`

**Fix:** Install in editable mode:
```bash
pip install -e ".[dev]"
```

### `pip install` fails with compilation errors

**Fix (macOS):**
```bash
xcode-select --install    # Install Xcode CLI tools
brew install openssl readline
```

**Fix (Ubuntu):**
```bash
sudo apt install -y build-essential libssl-dev libffi-dev python3-dev
```

### Redis connection refused

**Fix:** Redis isn't running. Start it:
```bash
# macOS
brew services start redis

# Ubuntu
sudo systemctl start redis-server

# Docker
docker run -d --name redis -p 6379:6379 redis:7-alpine
```

> Redis is optional for development — the app starts without it but caching won't work.

### `alembic upgrade head` fails

**Fix:** Ensure the database URL is correct in `.env`:
```bash
# For SQLite (default — creates file automatically)
DATABASE_URL=sqlite+aiosqlite:///./data/calendar_agent.db
```

### Tests fail after dependency update

**Fix:** Regenerate the lock file and reinstall:
```bash
pip install -e ".[dev]"
pip freeze | grep -v "^-e " > requirements.lock
make test-unit
```
