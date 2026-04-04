# Architecture Guide

> Calendar Management Agent — Clean/Hexagonal Architecture with Domain-Driven Design

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Layer Diagram](#layer-diagram)
3. [Domain Layer](#domain-layer)
4. [Application Layer](#application-layer)
5. [Infrastructure Layer](#infrastructure-layer)
6. [Agent Layer](#agent-layer)
7. [API Layer](#api-layer)
8. [Billing Layer](#billing-layer)
9. [Config Layer](#config-layer)
10. [Dependency Rules](#dependency-rules)
11. [Design Patterns Used](#design-patterns-used)

---

## Architecture Overview

This project implements **Clean Architecture** (also called Hexagonal / Ports & Adapters) combined with **Domain-Driven Design (DDD)** principles.

**Core principles:**

- **Domain at the center** — Business logic has zero external dependencies
- **Dependency Inversion** — Outer layers depend on inner layers, never the reverse
- **Ports & Adapters** — Domain defines *what* it needs (Ports); infrastructure provides *how* (Adapters)
- **Composition Root** — A single place (`config/container.py`) wires all layers together

---

## Layer Diagram

```
┌─────────────────────────────────────────────────────────┐
│                      API Layer                          │
│   FastAPI REST · WebSocket · Middleware (Rate Limiter)   │
│   → Thin layer: validates input, delegates to services  │
├─────────────────────────────────────────────────────────┤
│                    Agent Layer                           │
│   LangGraph State Machine · Tools · Intent Router       │
│   → AI logic: reason → act → observe → respond          │
├─────────────────────────────────────────────────────────┤
│                 Application Layer                        │
│   ChatService · CalendarService · AuthService · DTOs    │
│   → Orchestrates domain logic, enforces use cases       │
├─────────────────────────────────────────────────────────┤
│                  Billing Layer                           │
│   Plans · Usage Tracker · Stripe Integration            │
│   → SaaS metering and subscription management           │
├─────────────────────────────────────────────────────────┤
│                   Domain Layer                          │
│   Entities · Value Objects · Ports · Events · Errors    │
│   → Pure business logic, ZERO external dependencies     │
├─────────────────────────────────────────────────────────┤
│                Infrastructure Layer                     │
│   LLM Adapters · PostgreSQL · Redis · Google Calendar   │
│   → Concrete implementations of domain ports            │
└─────────────────────────────────────────────────────────┘
```

**Dependency flow:**

```
API → Application → Domain ← Infrastructure
       Agent → Application → Domain
       Billing → Domain
       Config wires everything (Composition Root)
```

---

## Domain Layer

**Location:** `src/domain/`
**Rule:** ZERO imports from any other layer. Only Python stdlib.

### Entities (`domain/entities/`)

Business objects with identity and lifecycle:

| Entity | File | Key Behaviors |
|---|---|---|
| `User` | `entities/user.py` | Plan-based access control, OAuth token management, request limits |
| `CalendarEvent` | `entities/calendar_event.py` | Conflict detection, attendee management, recurrence, reschedule |
| `Conversation` | `entities/conversation.py` | Sliding-window message history, context management |

**CalendarEvent example methods:**
- `conflicts_with(other)` — Time-overlap detection
- `add_attendee(email)` — Idempotent attendee addition
- `reschedule(start, end)` — Update times with validation
- `cancel()` — Soft-delete via status change
- `to_summary_string()` — Token-efficient string for LLM context

**User example methods:**
- `can_use_primary_model()` — Plan-gated access to expensive models
- `get_request_limit()` — Monthly quota based on subscription tier
- `has_valid_google_token()` — OAuth token expiry check

### Value Objects (`domain/value_objects/`)

Immutable data structures with no identity:

| Value Object | Purpose |
|---|---|
| `TimeSlot` | Start/end datetime pair with overlap detection |
| `WorkingHours` | User's working hours preference (time + weekdays) |
| `DateRange` | Validated date range for event queries |
| `TokenUsage` | LLM token tracking with multi-provider cost estimation |

**TokenUsage** includes pricing for both OpenAI and Anthropic models:
- `gpt-4o`: $2.50 / $10.00 per 1M tokens (input/output)
- `gpt-4o-mini`: $0.15 / $0.60 per 1M tokens
- `claude-sonnet-4-20250514`: $3.00 / $15.00 per 1M tokens
- `claude-haiku-3-20250414`: $0.25 / $1.25 per 1M tokens

### Interfaces / Ports (`domain/interfaces/`)

Abstract contracts that infrastructure must implement:

| Port | File | Methods |
|---|---|---|
| `LLMPort` | `interfaces/llm.py` | `chat_completion()`, `generate_embedding()`, `get_last_token_usage()` |
| `CachePort` | `interfaces/cache.py` | `get()`, `set()`, `delete()`, `exists()` |
| `CalendarProviderPort` | `interfaces/calendar_provider.py` | `list_events()`, `create_event()`, `update_event()`, `delete_event()`, `find_free_slots()` |
| `UserRepositoryPort` | `interfaces/user_repository.py` | `get_by_id()`, `get_by_email()`, `create()`, `update()` |
| `EventRepositoryPort` | `interfaces/event_repository.py` | `get_by_id()`, `list_by_user()`, `create()`, `update()`, `delete()` |
| `ConversationRepositoryPort` | `interfaces/conversation_repository.py` | `get_by_id()`, `create()`, `update()` |
| `UsageTrackerPort` | `interfaces/usage_tracker.py` | `record_request()`, `get_usage()`, `is_within_quota()` |

### Domain Events (`domain/events/`)

Emitted when significant state changes occur (decouples side effects):

| Event | Trigger |
|---|---|
| `EventCreated` | New calendar event created |
| `EventUpdated` | Calendar event modified |
| `EventDeleted` | Calendar event removed |
| `EventConflictDetected` | Scheduling conflict found |
| `QuotaWarning` | User approaching monthly limit |
| `QuotaExceeded` | User exceeded monthly limit |

### Domain Exceptions (`domain/exceptions/`)

Typed business-rule violations (mapped to HTTP errors in API layer):

| Exception | HTTP Status | Trigger |
|---|---|---|
| `EventNotFoundError` | 404 | Event doesn't exist |
| `EventConflictError` | 409 | Scheduling overlap |
| `EventInPastError` | 422 | Creating/modifying past events |
| `InvalidTimeRangeError` | 422 | Start > end time |
| `QuotaExceededError` | 429 | Monthly request limit hit |
| `AuthenticationError` | 401 | Invalid/expired token |
| `AuthorizationError` | 403 | Insufficient permissions |
| `CalendarProviderError` | 502 | Google API failure |

---

## Application Layer

**Location:** `src/application/`
**Rule:** Can import `domain`. Cannot import `infrastructure` directly.

### Services (`application/services/`)

| Service | Purpose |
|---|---|
| `ChatService` | Main orchestrator: quota → cache → route → agent → track → respond |
| `CalendarService` | Business logic for event CRUD (delegates to CalendarProviderPort) |
| `AuthService` | OAuth flow orchestration (delegates to auth ports) |

**ChatService Pipeline:**

```
User Message
  │
  ▼
1. Quota Check (UsageTrackerPort)
  │
  ▼
2. Get/Create Conversation (ConversationRepositoryPort)
  │
  ▼
3. Check Semantic Cache (CachePort)
  │  ├── HIT → Return cached response
  │  └── MISS ↓
  ▼
4. Classify Intent (IntentRouter)
  │  ├── DETERMINISTIC → Handle without LLM (zero cost)
  │  └── SIMPLE/MEDIUM/COMPLEX ↓
  ▼
5. Select Model (cost-tier routing)
  │  ├── SIMPLE/MEDIUM → Fast model (Haiku/Mini)
  │  └── COMPLEX → Primary model (Sonnet/GPT-4o)
  ▼
6. Execute Agent (LangGraph state machine)
  │
  ▼
7. Store in Conversation + Track Usage + Cache Response
  │
  ▼
Response
```

### DTOs (`application/dto/`)

Pydantic models for data transfer between layers:

| DTO | Direction | Fields |
|---|---|---|
| `ChatRequestDTO` | API → Application | `message`, `conversation_id`, `user_timezone` |
| `ChatResponseDTO` | Application → API | `message`, `conversation_id`, `tokens_used`, `model` |
| `CreateEventDTO` | API → Application | `title`, `start_time`, `end_time`, `description`, `attendees` |
| `EventResponseDTO` | Application → API | Full event fields |
| `UserProfileDTO` | Application → API | User info + plan + usage stats |
| `RequestComplexity` | Internal (enum) | `DETERMINISTIC`, `SIMPLE`, `MEDIUM`, `COMPLEX` |

---

## Infrastructure Layer

**Location:** `src/infrastructure/`
**Rule:** Implements domain ports. Can import domain interfaces.

### LLM Adapters (`infrastructure/llm/`)

Multi-provider support via **Factory + Strategy** pattern:

| File | Purpose |
|---|---|
| `factory.py` | `create_llm_adapter()` + `create_langchain_chat_model()` factories |
| `openai_adapter.py` | `OpenAIAdapter` — implements `LLMPort` for GPT models |
| `anthropic_adapter.py` | `AnthropicAdapter` — implements `LLMPort` for Claude models |

See [LLM_PROVIDERS.md](LLM_PROVIDERS.md) for full details on adding new providers.

### Persistence (`infrastructure/persistence/`)

| File | Purpose |
|---|---|
| `database.py` | `Database` class — async SQLAlchemy engine + session factory |
| `models.py` | SQLAlchemy ORM models (User, Event, Conversation tables) |
| `user_repository.py` | `SQLAlchemyUserRepository` — implements `UserRepositoryPort` |

- **Dev:** SQLite via `aiosqlite`
- **Prod:** PostgreSQL via `asyncpg`

### Cache (`infrastructure/cache/`)

| File | Purpose |
|---|---|
| `redis_cache.py` | `RedisCacheAdapter` — implements `CachePort` |

Features: JSON serialization, configurable TTL, LRU eviction.

### Auth (`infrastructure/auth/`)

| File | Purpose |
|---|---|
| `jwt_service.py` | JWT creation + verification (access + refresh tokens) |
| `google_oauth.py` | Google OAuth2 flow (authorization URL, token exchange) |

### Calendar Providers (`infrastructure/calendar_providers/`)

| File | Purpose |
|---|---|
| `google_calendar.py` | `GoogleCalendarAdapter` — implements `CalendarProviderPort` |

---

## Agent Layer

**Location:** `src/agent/`
**Rule:** Can import `application` services. Houses AI-specific logic.

### State Machine (`agent/graph.py`)

LangGraph implementation of the **ReAct** (Reason + Act) pattern:

```
                ┌──────────┐
                │  reason   │ ← LLM decides next action
                └─────┬─────┘
                      │
              ┌───────┴───────┐
              │  should_continue │
              └───┬───────┬───┘
                  │       │
            tools │       │ end
                  ▼       ▼
            ┌─────────┐  ┌─────┐
            │  tools   │  │ END │
            └────┬────┘  └─────┘
                 │
                 └──────→ back to reason
```

The agent dynamically selects the LLM model via the **factory pattern** — same graph works with any provider.

### Tools (`agent/tools/calendar_tools.py`)

Six LangChain tools exposed to the agent:

| Tool | Description |
|---|---|
| `list_events` | List events in a date range |
| `create_event` | Create a new calendar event |
| `update_event` | Modify an existing event |
| `delete_event` | Remove an event |
| `find_free_slots` | Find available time windows |
| `check_conflicts` | Detect scheduling overlaps |

### Intent Router (`agent/router/`)

Rule-based classifier for cost-optimized routing (zero LLM cost):

| Pattern Type | Examples | Routing |
|---|---|---|
| **Deterministic** | "What's my schedule today?", "Show my agenda" | Direct API call (no LLM) |
| **Simple** | "Create a meeting at 3pm", "Cancel the standup" | Fast model (Haiku/Mini) |
| **Complex** | "Reorganize my week", "Resolve all conflicts" | Primary model (Sonnet/GPT-4o) |
| **Medium** | Everything else | Fast model (default) |

### Prompts (`agent/prompts.py`)

Token-efficient system prompt template with dynamic context injection:
- Current date/day
- User timezone
- Working hours
- Calendar context (compressed event data)

---

## API Layer

**Location:** `src/api/`
**Rule:** Thin layer. Validates input, delegates to application services.

### REST (`api/rest/`)

- **`app.py`** — FastAPI application factory with lifespan, CORS, rate limiting
- **`routes.py`** — 13 endpoints across 4 routers (health, auth, chat, calendar)

### WebSocket (`api/websocket/`)

- **`chat_ws.py`** — Streaming chat endpoint for real-time agent responses

### Middleware (`api/middleware/`)

- **`rate_limiter.py`** — Per-IP rate limiting middleware

---

## Billing Layer

**Location:** `src/billing/`
**Rule:** Uses domain interfaces for usage tracking.

See [BILLING.md](BILLING.md) for full details.

---

## Config Layer

**Location:** `src/config/`
**Role:** The ONLY place that wires everything together.

### Settings (`config/settings.py`)

- Pydantic `BaseSettings` loaded from `.env`
- All configuration centralized: app, database, Redis, LLM providers, Google OAuth, Stripe, JWT, agent
- `active_*` properties resolve correct values based on `LLM_PROVIDER`

### Container (`config/container.py`)

**Composition Root** — creates and holds all service instances:

```python
Container(settings)
  ├── .database()        → Database (SQLAlchemy async engine)
  ├── .cache()           → RedisCacheAdapter
  ├── .jwt_service()     → JWTService
  ├── .google_oauth()    → GoogleOAuthService
  ├── .llm_adapter()     → LLMPort (via factory — Anthropic or OpenAI)
  ├── .usage_tracker()   → RedisUsageTracker
  ├── .intent_router()   → IntentRouter
  └── .shutdown()        → Cleanup all resources
```

All instances are **lazy-initialized** and **cached** (singleton per container lifetime).

---

## Dependency Rules

| Layer | Can Import | Cannot Import |
|---|---|---|
| `domain` | Python stdlib only | application, infrastructure, agent, api, config |
| `application` | domain | infrastructure, agent, api (directly) |
| `infrastructure` | domain (interfaces) | application, agent, api |
| `agent` | application, domain | infrastructure (directly) |
| `api` | application, domain | infrastructure (directly) |
| `billing` | domain (interfaces) | infrastructure (directly) |
| `config` | ALL (composition root) | — |

**Key principle:** Inner layers never know about outer layers. All dependencies point inward.

---

## Design Patterns Used

| Pattern | Where | Purpose |
|---|---|---|
| **Hexagonal / Ports & Adapters** | Domain ↔ Infrastructure | Decouple business logic from tech choices |
| **Factory** | `infrastructure/llm/factory.py` | Create correct LLM adapter based on config |
| **Strategy** | LLM adapters | Same interface, different provider implementations |
| **Repository** | `domain/interfaces/*_repository.py` | Abstract data access |
| **Composition Root** | `config/container.py` | Single place to wire all dependencies |
| **DTO** | `application/dto/` | Clean data transfer between layers |
| **Domain Events** | `domain/events/` | Decouple side effects from business logic |
| **ReAct** | `agent/graph.py` | Reason → Act → Observe loop for AI agent |
| **Singleton** | `Settings`, Container instances | One instance per app lifecycle |
| **Middleware** | `api/middleware/` | Cross-cutting concerns (rate limiting, auth) |
