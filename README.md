# 📅 Calendar Management Agent — SaaS Platform

AI-powered calendar management through natural language, built as a multi-tenant SaaS application with **Clean/Hexagonal Architecture** and **multi-provider LLM support** (Anthropic Claude, OpenAI GPT, extensible to future providers).

> **Status:** Core scaffold complete · 46/46 unit tests passing · 13 API routes active

---

## 📖 Documentation

| Document | Description |
|---|---|
| **You are here** | Project overview, quick start, tech stack |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Hexagonal architecture, DDD, layer rules, dependency flow |
| [docs/LLM_PROVIDERS.md](docs/LLM_PROVIDERS.md) | Multi-provider LLM setup, factory pattern, adding new providers |
| [docs/COST_OPTIMIZATION.md](docs/COST_OPTIMIZATION.md) | Tiered routing, deterministic shortcuts, caching, token compression |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | REST endpoints, WebSocket, request/response schemas |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker, CI/CD, cloud deployment, environment config |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local setup, testing, linting, migrations, Makefile |
| [docs/BILLING.md](docs/BILLING.md) | SaaS plans, Stripe integration, usage tracking |

---

## Key Features

- 🤖 **AI Agent** — LangGraph ReAct agent with tool calling (reason → act → observe loop)
- 🔌 **Multi-Provider LLM** — Anthropic Claude & OpenAI GPT via Factory pattern; add new providers in minutes
- 📅 **Calendar CRUD** — Create, update, delete, list events via Google Calendar API
- 🧠 **Smart Scheduling** — Conflict detection, free slot finder, auto-reschedule
- 💰 **Cost Optimized** — Tiered model routing, deterministic shortcuts, semantic caching
- 🏢 **Multi-Tenant SaaS** — Per-tenant isolation, usage tracking, plan-based limits
- 💳 **Billing** — Stripe subscriptions (Free / Pro / Business / Enterprise)
- 🔐 **Secure** — Google OAuth2, JWT auth, encrypted token storage
- 📡 **Real-time** — WebSocket streaming for agent responses

---

## Quick Start

### Prerequisites

- Python 3.11+ (project uses `pyenv` with 3.11.7)
- Redis (caching + usage tracking)
- PostgreSQL (production) or SQLite (development — default)

### 1. Install & Configure

```bash
# Clone and enter project
cd CalendarManagementAI

# Create virtual environment
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows

# Install all dependencies (including dev tools)
pip install -e ".[dev]"

# Copy environment config
cp .env.example .env
```

### 2. Set Your LLM Provider

Edit `.env` — only one API key is required:

```bash
# Choose your provider: anthropic | openai
LLM_PROVIDER=anthropic

# Anthropic (if using Claude)
ANTHROPIC_API_KEY=sk-ant-your-key-here

# OR OpenAI (if using GPT)
# LLM_PROVIDER=openai
# OPENAI_API_KEY=sk-your-key-here
```

### 3. Run

```bash
# Run database migrations
alembic upgrade head

# Start the dev server (auto-reload)
make run
# → http://localhost:8000/docs  (Swagger UI)

# Run tests
make test-unit     # 46 unit tests
make test-cov      # With coverage report
```

### 4. Docker (full stack)

```bash
make docker-up     # App + PostgreSQL + Redis
make docker-down   # Stop all services
make docker-logs   # Tail logs
```

---

## Project Structure

```
CalendarManagementAI/
├── src/
│   ├── domain/                  # 🟢 Pure business logic (ZERO external deps)
│   │   ├── entities/            #    User, CalendarEvent, Conversation
│   │   ├── value_objects/       #    TimeSlot, WorkingHours, TokenUsage, DateRange
│   │   ├── interfaces/          #    Ports: LLMPort, CachePort, CalendarProviderPort, ...
│   │   ├── events/              #    Domain events (EventCreated, ConflictDetected, ...)
│   │   └── exceptions/          #    Typed errors (QuotaExceededError, EventNotFoundError, ...)
│   ├── application/             # 🔵 Use cases & orchestration
│   │   ├── services/            #    CalendarService, ChatService, AuthService
│   │   └── dto/                 #    Data Transfer Objects (Pydantic)
│   ├── infrastructure/          # 🟠 Adapters (concrete implementations)
│   │   ├── llm/                 #    OpenAI adapter, Anthropic adapter, LLM factory
│   │   ├── persistence/         #    SQLAlchemy models, Database, repositories
│   │   ├── calendar_providers/  #    Google Calendar API adapter
│   │   ├── cache/               #    Redis cache adapter
│   │   └── auth/                #    JWT service, Google OAuth
│   ├── agent/                   # 🟣 AI Agent core
│   │   ├── graph.py             #    LangGraph state machine (ReAct pattern)
│   │   ├── tools/               #    6 calendar tools for the agent
│   │   ├── router/              #    Intent classifier + complexity router
│   │   ├── prompts.py           #    System prompt templates
│   │   └── state.py             #    AgentState TypedDict
│   ├── api/                     # 🔴 Interface layer
│   │   ├── rest/                #    FastAPI app factory + routes (13 endpoints)
│   │   ├── websocket/           #    Streaming chat WebSocket
│   │   └── middleware/          #    Rate limiting, auth middleware
│   ├── billing/                 # 💳 SaaS billing
│   │   ├── plans.py             #    Plan tiers (Free/Pro/Business/Enterprise)
│   │   ├── usage_tracker.py     #    Redis-backed per-tenant metering
│   │   └── stripe_service.py    #    Stripe subscription management
│   └── config/                  # ⚙️ Settings + DI container
│       ├── settings.py          #    Pydantic Settings (from .env)
│       └── container.py         #    Composition root (wires all layers)
├── tests/
│   ├── unit/                    # 46 unit tests (no external deps)
│   └── integration/             # Integration tests (requires services)
├── migrations/                  # Alembic database migrations
├── .github/workflows/           # CI (lint + test) & Deploy (Docker + GHCR)
├── docker-compose.yml           # App + PostgreSQL + Redis
├── Dockerfile                   # Multi-stage production build
├── Makefile                     # Dev commands
└── pyproject.toml               # Dependencies & tool config
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| **AI Agent** | LangGraph + LangChain (ReAct pattern with tool calling) |
| **LLM Providers** | Anthropic Claude (default) · OpenAI GPT · Extensible via factory |
| **Web Framework** | FastAPI + Uvicorn (async) |
| **Database** | PostgreSQL (prod) · SQLite (dev) · SQLAlchemy 2.0 async ORM |
| **Cache** | Redis (semantic cache, usage tracking, rate limiting) |
| **Auth** | Google OAuth2 + JWT (python-jose) |
| **Billing** | Stripe (subscriptions + webhooks) |
| **CI/CD** | GitHub Actions (lint → test → build → push to GHCR) |
| **Deployment** | Docker · Cloud Run / ECS ready |
| **Monitoring** | Sentry + LangSmith + structlog |

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/ready` | Readiness probe |
| `POST` | `/api/v1/chat/` | Send message to AI agent |
| `GET` | `/api/v1/auth/google/login` | Initiate Google OAuth2 flow |
| `GET` | `/api/v1/auth/google/callback` | OAuth callback |
| `GET` | `/api/v1/auth/me` | Current user profile + usage stats |
| `GET` | `/api/v1/calendar/events` | List events (date range) |
| `POST` | `/api/v1/calendar/events` | Create event |
| `DELETE` | `/api/v1/calendar/events/{id}` | Delete event |
| `WS` | `/ws/chat` | WebSocket streaming chat |

> Full details with schemas: [docs/API_REFERENCE.md](docs/API_REFERENCE.md)

---

## SaaS Plans

| Plan | Price/mo | Requests/mo | LLM Models | Key Features |
|---|---|---|---|---|
| **Free** | $0 | 50 | Fast only (Haiku/Mini) | Basic CRUD, single calendar |
| **Pro** | $9.99 | 500 | Fast + Primary | Smart scheduling, multi-calendar |
| **Business** | $29.99 | 2,000 | Fast + Primary | Team calendars, API access, webhooks |
| **Enterprise** | Custom | 100,000 | All models | Dedicated instance, SLA, SSO |

> Full billing docs: [docs/BILLING.md](docs/BILLING.md)

---

## License

MIT
