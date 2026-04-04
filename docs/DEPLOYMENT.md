# Deployment Guide

> Docker, CI/CD pipelines, cloud deployment, and environment configuration.

---

## Table of Contents

1. [Environment Configuration](#environment-configuration)
2. [Local Development](#local-development)
3. [Docker](#docker)
4. [CI/CD Pipeline](#cicd-pipeline)
5. [Cloud Deployment](#cloud-deployment)
6. [Production Checklist](#production-checklist)

---

## Environment Configuration

All configuration is driven by environment variables, loaded via Pydantic Settings from a `.env` file.

### Full .env Reference

```bash
# === Application ===
APP_ENV=development                  # development | staging | production
APP_SECRET_KEY=change-me-to-a-random-secret  # ⚠️ MUST change in production
APP_PORT=8000
APP_LOG_LEVEL=INFO                   # DEBUG | INFO | WARNING | ERROR
APP_CORS_ORIGINS=["http://localhost:3000"]

# === Database ===
# Development (SQLite — zero setup)
DATABASE_URL=sqlite+aiosqlite:///./data/calendar_agent.db

# Production (PostgreSQL — recommended)
# DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/calendar_agent

# === Redis ===
REDIS_URL=redis://localhost:6379/0

# === LLM Provider ===
LLM_PROVIDER=anthropic              # anthropic | openai
LLM_TEMPERATURE=0.1                 # Lower = more deterministic
LLM_MAX_TOKENS=4096                 # Max output tokens

# === Anthropic (Claude) ===
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL_PRIMARY=claude-sonnet-4-20250514
ANTHROPIC_MODEL_FAST=claude-haiku-3-20250414

# === OpenAI (GPT) ===
OPENAI_API_KEY=                      # Leave blank if not using
OPENAI_MODEL_PRIMARY=gpt-4o
OPENAI_MODEL_FAST=gpt-4o-mini

# === Google Calendar OAuth ===
GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=http://localhost:8000/api/v1/auth/google/callback
GOOGLE_SCOPES=https://www.googleapis.com/auth/calendar

# === Agent Behavior ===
AGENT_MAX_ITERATIONS=10              # Max tool-call loops per request
AGENT_RECURSION_LIMIT=25             # LangGraph recursion limit
AGENT_DEFAULT_TIMEZONE=UTC
AGENT_WORKING_HOURS_START=09:00
AGENT_WORKING_HOURS_END=17:00
AGENT_BUFFER_MINUTES=15              # Buffer between scheduled events
AGENT_CACHE_TTL_SECONDS=300          # Response cache TTL (5 min)

# === Billing / Stripe ===
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_PRO=price_...
STRIPE_PRICE_BUSINESS=price_...

# === Monitoring ===
LANGSMITH_API_KEY=                   # Optional: LangChain tracing
LANGSMITH_PROJECT=calendar-agent
SENTRY_DSN=                          # Optional: Error tracking

# === JWT ===
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
```

### Environment Differences

| Setting | Development | Staging | Production |
|---|---|---|---|
| `APP_ENV` | development | staging | production |
| `DATABASE_URL` | SQLite | PostgreSQL | PostgreSQL |
| `APP_SECRET_KEY` | any string | random secret | strong random secret |
| Swagger UI (`/docs`) | Enabled | Enabled | Disabled |
| `APP_LOG_LEVEL` | DEBUG | INFO | WARNING |
| `LLM_PROVIDER` | your choice | your choice | your choice |

---

## Local Development

### Quick Start

```bash
# Install Python 3.11+ (recommended: pyenv)
pyenv install 3.11.7
pyenv local 3.11.7

# Create venv and install
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env with your API keys

# Run migrations
alembic upgrade head

# Start dev server (auto-reload)
make run
# → http://localhost:8000/docs
```

### Without Redis (development only)

The app will start without Redis but caching and usage tracking won't work. SQLite is used by default for the database — no PostgreSQL needed for development.

---

## Docker

### Architecture

```
docker-compose.yml
  ├── app        → Calendar Agent (FastAPI + Uvicorn)
  ├── postgres   → PostgreSQL 16 (data persistence)
  └── redis      → Redis 7 (cache + usage tracking)
```

### Commands

```bash
# Start all services (builds image if needed)
make docker-up
# or: docker compose up -d --build

# View logs
make docker-logs
# or: docker compose logs -f app

# Stop all services
make docker-down
# or: docker compose down

# Stop and remove volumes (⚠️ destroys data)
docker compose down -v
```

### Dockerfile Details

Multi-stage build for minimal production image:

```
Stage 1: Builder
  ├── python:3.12-slim base
  ├── Install build tools
  └── pip install dependencies

Stage 2: Runtime
  ├── python:3.12-slim base (clean)
  ├── Copy only installed packages from builder
  ├── Copy application source
  ├── Non-root user (security)
  ├── Health check (HTTP /health)
  └── CMD: uvicorn with factory
```

**Image size:** ~250MB (production, no dev tools)

### docker-compose.yml Services

| Service | Image | Ports | Health Check | Persistence |
|---|---|---|---|---|
| `app` | Build from Dockerfile | 8000:8000 | HTTP /health | `app-data` volume |
| `postgres` | postgres:16-alpine | 5432:5432 | `pg_isready` | `postgres-data` volume |
| `redis` | redis:7-alpine | 6379:6379 | `redis-cli ping` | `redis-data` volume |

Redis config: `appendonly yes`, 256MB max memory, LRU eviction.

### Environment in Docker

The app container loads `.env` via `env_file: .env`. For production, use Docker secrets or a secrets manager instead:

```yaml
# docker-compose.prod.yml
services:
  app:
    environment:
      - APP_SECRET_KEY_FILE=/run/secrets/app_secret
    secrets:
      - app_secret
```

---

## CI/CD Pipeline

### GitHub Actions Workflows

Two workflows in `.github/workflows/`:

#### 1. CI (`ci.yml`) — Lint + Test

**Triggers:** Push to `main`/`develop`, Pull requests to `main`

```
┌─────────┐     ┌──────────┐
│  lint    │────→│   test   │
└─────────┘     └──────────┘
```

**Lint job:**
- Ruff check (linting rules)
- Ruff format check (code style)

**Test job (depends on lint):**
- Redis service container for integration tests
- Unit tests (`pytest -m unit`)
- Integration tests (`pytest -m integration`)
- Coverage report → Codecov

#### 2. Deploy (`deploy.yml`) — Build + Push Image

**Triggers:** Push to `main`, version tags (`v*`)

```
┌──────────────┐     ┌────────────────────┐
│  Build image │────→│  Push to GHCR      │
│  (Buildx)    │     │  (ghcr.io/owner/   │
│              │     │   repo:latest)      │
└──────────────┘     └────────────────────┘
```

- Docker Buildx for multi-platform builds
- GitHub Container Registry (GHCR)
- Tags: `latest` + commit SHA
- GitHub Actions cache for faster builds

### Required GitHub Secrets

| Secret | Purpose |
|---|---|
| `GITHUB_TOKEN` | Auto-provided, for GHCR login |
| `ANTHROPIC_API_KEY` | For integration tests (optional) |
| `OPENAI_API_KEY` | For integration tests (optional) |
| `CODECOV_TOKEN` | Coverage upload (optional) |

---

## Cloud Deployment

### Option 1: Google Cloud Run

**Recommended for:** Small to medium traffic, auto-scaling, pay-per-request.

```bash
# Build and push to Google Artifact Registry
gcloud builds submit --tag gcr.io/PROJECT_ID/calendar-agent

# Deploy
gcloud run deploy calendar-agent \
  --image gcr.io/PROJECT_ID/calendar-agent \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars "APP_ENV=production,LLM_PROVIDER=anthropic" \
  --set-secrets "APP_SECRET_KEY=app-secret:latest,ANTHROPIC_API_KEY=anthropic-key:latest"
```

**External services needed:**
- Cloud SQL (PostgreSQL) or Neon/Supabase
- Memorystore (Redis) or Upstash

### Option 2: AWS ECS (Fargate)

**Recommended for:** Production workloads, predictable traffic, fine-grained control.

```bash
# Push to ECR
aws ecr get-login-password | docker login --username AWS --password-stdin ACCOUNT.dkr.ecr.REGION.amazonaws.com
docker tag calendar-agent:latest ACCOUNT.dkr.ecr.REGION.amazonaws.com/calendar-agent:latest
docker push ACCOUNT.dkr.ecr.REGION.amazonaws.com/calendar-agent:latest

# Deploy via ECS task definition + service
# (use Terraform or CDK for infrastructure-as-code)
```

**External services needed:**
- RDS (PostgreSQL)
- ElastiCache (Redis)
- Secrets Manager

### Option 3: Railway / Render / Fly.io

**Recommended for:** Quick deployment, minimal ops.

Most PaaS platforms support Docker deployments:

```bash
# Railway
railway login
railway link
railway up

# Fly.io
fly launch
fly deploy
```

### Database Hosting Options

| Provider | Type | Free Tier | Best For |
|---|---|---|---|
| Neon | Serverless PostgreSQL | 512MB | Dev/Small prod |
| Supabase | PostgreSQL + extras | 500MB | Full-stack |
| AWS RDS | Managed PostgreSQL | 12 months | Production |
| Cloud SQL | Managed PostgreSQL | None | GCP projects |

### Redis Hosting Options

| Provider | Free Tier | Best For |
|---|---|---|
| Upstash | 10K commands/day | Dev/Small prod |
| Redis Cloud | 30MB | Small workloads |
| ElastiCache | None | AWS production |
| Memorystore | None | GCP production |

---

## Production Checklist

### Security

- [ ] Generate strong `APP_SECRET_KEY` (use `python -c "import secrets; print(secrets.token_urlsafe(64))"`)
- [ ] Set `APP_ENV=production` (disables Swagger UI)
- [ ] Store secrets in a secrets manager (not `.env` files)
- [ ] Enable HTTPS (via reverse proxy or cloud load balancer)
- [ ] Restrict `APP_CORS_ORIGINS` to your frontend domain
- [ ] Set up rate limiting (already built into middleware)

### Database

- [ ] Use PostgreSQL (not SQLite) for production
- [ ] Run migrations: `alembic upgrade head`
- [ ] Set up automated backups
- [ ] Enable connection pooling (PgBouncer or built-in)

### Monitoring

- [ ] Set `SENTRY_DSN` for error tracking
- [ ] Set `LANGSMITH_API_KEY` for LLM call tracing
- [ ] Set up health check monitoring on `/health` and `/ready`
- [ ] Configure alerting on error rates and latency

### Performance

- [ ] Redis `maxmemory` set appropriately (256MB minimum recommended)
- [ ] Database connection pool sized for expected concurrency
- [ ] Uvicorn workers: `--workers $(nproc)` for multi-core utilization
- [ ] Enable Docker health checks

### Billing

- [ ] Set Stripe keys (`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`)
- [ ] Create Stripe products and prices for each plan tier
- [ ] Set price IDs (`STRIPE_PRICE_PRO`, `STRIPE_PRICE_BUSINESS`)
- [ ] Configure Stripe webhook endpoint: `POST /api/v1/billing/webhook`
