# Development Guide

> Local setup, testing, linting, database migrations, and everyday development commands.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Initial Setup](#initial-setup)
3. [Project Structure](#project-structure)
4. [Running the Application](#running-the-application)
5. [Testing](#testing)
6. [Linting & Formatting](#linting--formatting)
7. [Database Migrations](#database-migrations)
8. [Makefile Commands](#makefile-commands)
9. [Adding New Features](#adding-new-features)
10. [Debugging](#debugging)

---

## Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.11+ | Runtime (recommend 3.11.7 via pyenv) |
| pyenv | Latest | Python version management (recommended) |
| Redis | 7+ | Cache + usage tracking (optional for dev) |
| PostgreSQL | 16+ | Production database (SQLite for dev) |
| Docker | 24+ | Containerized deployment (optional) |

### Installing Python 3.11 via pyenv

```bash
# Install pyenv (macOS)
brew install pyenv

# Install Python 3.11.7
pyenv install 3.11.7
pyenv local 3.11.7    # Sets .python-version file

# Verify
python --version      # → Python 3.11.7
```

---

## Initial Setup

```bash
# 1. Create virtual environment
python -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate       # Windows

# 2. Install all dependencies (including dev tools)
pip install -e ".[dev]"

# 3. Copy environment file
cp .env.example .env
# Edit .env with your API keys (at minimum: LLM_PROVIDER + API key)

# 4. Install pre-commit hooks
pre-commit install

# 5. Run database migrations
alembic upgrade head

# 6. Verify everything works
make test-unit                 # Should pass 46 tests
make run                       # Start dev server
```

---

## Project Structure

```
CalendarManagementAI/
├── src/                        # Application source code
│   ├── domain/                 # 🟢 Business logic (no external deps)
│   ├── application/            # 🔵 Use cases & orchestration
│   ├── infrastructure/         # 🟠 Adapters (DB, LLM, cache, auth)
│   ├── agent/                  # 🟣 AI agent (LangGraph, tools, router)
│   ├── api/                    # 🔴 HTTP/WS interface (FastAPI)
│   ├── billing/                # 💳 SaaS billing (Stripe, plans)
│   └── config/                 # ⚙️ Settings + DI container
├── tests/
│   ├── unit/                   # Unit tests (no external deps needed)
│   │   ├── test_calendar_event.py
│   │   ├── test_user.py
│   │   ├── test_value_objects.py
│   │   ├── test_intent_router.py
│   │   └── test_llm_factory.py
│   └── integration/            # Integration tests (require Redis, DB)
│       └── test_api.py
├── migrations/                 # Alembic migration files
│   └── env.py
├── .github/workflows/          # CI/CD pipelines
├── docs/                       # Documentation (you are here)
├── pyproject.toml              # Dependencies + tool configuration
├── Makefile                    # Development commands
├── Dockerfile                  # Production container
├── docker-compose.yml          # Full-stack local setup
├── alembic.ini                 # Database migration config
├── .env.example                # Environment variable template
├── .gitignore                  # Git ignore rules
└── .python-version             # pyenv Python version
```

---

## Running the Application

### Development Server

```bash
make run
# Equivalent to: uvicorn src.api.rest.app:create_app --factory --host 0.0.0.0 --port 8000 --reload
```

This starts a development server with:
- **Auto-reload** on code changes
- **Swagger UI** at http://localhost:8000/docs
- **ReDoc** at http://localhost:8000/redoc
- **Health check** at http://localhost:8000/health

### With Docker (full stack)

```bash
make docker-up     # Starts app + PostgreSQL + Redis
make docker-logs   # Tail logs
make docker-down   # Stop everything
```

---

## Testing

### Test Structure

Tests are organized by type and marked with pytest markers:

| Marker | Location | Dependencies | Command |
|---|---|---|---|
| `@pytest.mark.unit` | `tests/unit/` | None (mocked) | `make test-unit` |
| `@pytest.mark.integration` | `tests/integration/` | Redis, DB | `make test-integration` |
| `@pytest.mark.e2e` | `tests/e2e/` | All services | — |

### Running Tests

```bash
# All tests
make test

# Unit tests only (fast, no external deps)
make test-unit

# Integration tests (requires Redis)
make test-integration

# With coverage report
make test-cov
# → Opens htmlcov/index.html

# Verbose output
pytest tests/unit/ -v -m unit --tb=long

# Run a specific test file
pytest tests/unit/test_calendar_event.py -v

# Run a specific test
pytest tests/unit/test_calendar_event.py::TestCalendarEvent::test_conflicts_with_overlapping -v
```

### Current Test Suite (46 tests)

| File | Tests | What's Tested |
|---|---|---|
| `test_calendar_event.py` | 10 | Entity: conflict detection, attendees, reschedule, cancel |
| `test_user.py` | 8 | Entity: plan limits, OAuth tokens, model access |
| `test_value_objects.py` | 9 | TimeSlot, DateRange, TokenUsage (OpenAI + Claude pricing) |
| `test_intent_router.py` | 8 | Deterministic/simple/complex classification |
| `test_llm_factory.py` | 7 | Factory: provider creation, enum, defaults, errors |

### Writing New Tests

```python
"""Tests for {feature} — describe what's being tested."""

from __future__ import annotations

import pytest


class TestFeatureName:
    """Test group for feature."""

    @pytest.mark.unit
    def test_specific_behavior(self) -> None:
        """Test that {specific behavior} works correctly."""
        # Arrange
        ...
        # Act
        result = ...
        # Assert
        assert result == expected

    @pytest.mark.unit
    async def test_async_behavior(self) -> None:
        """Async tests are auto-detected (asyncio_mode = auto)."""
        result = await some_async_function()
        assert result is not None
```

### Test Configuration

From `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"           # Auto-detect async tests
addopts = "-v --tb=short --strict-markers"
markers = [
    "unit: Unit tests (no external deps)",
    "integration: Integration tests (requires services)",
    "e2e: End-to-end tests",
]
```

---

## Linting & Formatting

### Tools

| Tool | Purpose | Config |
|---|---|---|
| **Ruff** | Linting + formatting (replaces flake8, isort, black) | `pyproject.toml` |
| **mypy** | Static type checking | `pyproject.toml` |
| **pre-commit** | Auto-run on git commit | `.pre-commit-config.yaml` |

### Commands

```bash
# Lint (check for issues)
make lint
# Equivalent to: ruff check src/ tests/ && mypy src/

# Format (auto-fix)
make format
# Equivalent to: ruff format src/ tests/ && ruff check --fix src/ tests/
```

### Ruff Configuration

From `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py311"
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = [
    "E",    # pycodestyle errors
    "F",    # pyflakes
    "W",    # pycodestyle warnings
    "I",    # isort (import ordering)
    "N",    # pep8-naming
    "UP",   # pyupgrade
    "B",    # bugbear
    "A",    # builtins
    "SIM",  # simplify
    "TCH",  # type-checking imports
]
ignore = ["E501"]  # Line length handled by formatter
```

### mypy Configuration

```toml
[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
disallow_untyped_defs = true
plugins = ["pydantic.mypy"]
```

---

## Database Migrations

Using [Alembic](https://alembic.sqlalchemy.org/) for schema migrations.

### Commands

```bash
# Apply all pending migrations
make migrate-up
# Equivalent to: alembic upgrade head

# Rollback last migration
make migrate-down
# Equivalent to: alembic downgrade -1

# Create a new migration
make migrate-create MSG="add user preferences table"
# Equivalent to: alembic revision --autogenerate -m "add user preferences table"
```

### Migration Workflow

1. Modify SQLAlchemy models in `src/infrastructure/persistence/models.py`
2. Generate migration: `make migrate-create MSG="describe changes"`
3. Review generated file in `migrations/versions/`
4. Apply: `make migrate-up`
5. Test: `make test`

### Configuration

- `alembic.ini` — Connection string (overridden by `DATABASE_URL` env var)
- `migrations/env.py` — Async migration environment

---

## Makefile Commands

Complete reference of all `make` targets:

| Command | Description |
|---|---|
| `make help` | Show all available commands |
| `make install` | Install production dependencies |
| `make dev` | Install dev dependencies + pre-commit hooks |
| `make test` | Run all tests |
| `make test-unit` | Run unit tests only |
| `make test-integration` | Run integration tests only |
| `make test-cov` | Run tests with HTML coverage report |
| `make lint` | Run Ruff linter + mypy |
| `make format` | Auto-format code with Ruff |
| `make run` | Start dev server (auto-reload) |
| `make migrate-up` | Apply all database migrations |
| `make migrate-down` | Rollback last migration |
| `make migrate-create` | Create new migration (use `MSG="..."`) |
| `make docker-up` | Start Docker Compose stack |
| `make docker-down` | Stop Docker Compose stack |
| `make docker-logs` | Tail Docker logs |
| `make clean` | Remove build artifacts, caches, pyc files |

---

## Adding New Features

### Layer Checklist

When adding a new feature, follow this order:

1. **Domain** — Define entities, value objects, interfaces (ports)
2. **Application** — Create service methods, DTOs
3. **Infrastructure** — Implement adapters (DB, external APIs)
4. **Agent** — Add tools if the AI agent needs the feature
5. **API** — Add REST/WebSocket endpoints
6. **Tests** — Unit tests for domain + application, integration for infra

### Example: Adding Microsoft Calendar Support

```
1. Domain: CalendarProviderPort already exists (no changes needed)
2. Application: CalendarService already uses the port (no changes needed)
3. Infrastructure: Create src/infrastructure/calendar_providers/microsoft.py
   → Implements CalendarProviderPort using Microsoft Graph API
4. Config: Add settings for Microsoft credentials
5. Container: Wire based on a CALENDAR_PROVIDER setting
6. Tests: Unit tests for the new adapter
```

### Coding Conventions

- Python 3.11+ with `from __future__ import annotations`
- Async-first (`async/await` everywhere)
- Pydantic for DTOs and settings
- Type hints on all functions
- Dataclasses for domain entities (not Pydantic — keeps domain pure)
- Tests marked with `@pytest.mark.unit` or `@pytest.mark.integration`

---

## Debugging

### FastAPI Debug Mode

```bash
APP_LOG_LEVEL=DEBUG make run
```

### Testing a Single Component

```python
# Quick test of the LLM factory
python -c "
from src.infrastructure.llm.factory import create_llm_adapter
adapter = create_llm_adapter('anthropic', 'test-key')
print(type(adapter).__name__)  # → AnthropicAdapter
"
```

### Checking App Bootstrap

```python
python -c "
from src.api.rest.app import create_app
app = create_app()
routes = [r.path for r in app.routes if hasattr(r, 'path')]
print(f'{len(routes)} routes loaded')
for r in sorted(routes):
    print(f'  {r}')
"
```

### Common Issues

| Issue | Solution |
|---|---|
| `ModuleNotFoundError: No module named 'src'` | Install in dev mode: `pip install -e ".[dev]"` |
| `Python 3.11+ required` | Use pyenv: `pyenv install 3.11.7 && pyenv local 3.11.7` |
| Tests fail with import errors | Ensure `.venv` is activated and deps installed |
| Redis connection refused | Start Redis: `redis-server` or `docker compose up redis` |
| Alembic can't find models | Check `migrations/env.py` imports |
