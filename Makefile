.PHONY: help install dev test test-fast test-unit test-integration test-cov lint format run serve migrate-init migrate-create migrate-up migrate-down docker-up docker-down docker-logs clean

PYTHON := $(shell [ -f .venv/bin/python ] && echo .venv/bin/python || echo python)
PIP    := $(shell [ -f .venv/bin/pip ]    && echo .venv/bin/pip    || echo pip)
PYTEST := $(shell [ -f .venv/bin/pytest ] && echo .venv/bin/pytest  || echo pytest)
TESTING ?= 1  ## Set to 0 to re-enable rate limiting during tests

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install production dependencies
	$(PIP) install -e .

dev: ## Install development dependencies
	$(PIP) install -e ".[dev]"
	pre-commit install

test: ## Run all tests with coverage
	TESTING=$(TESTING) $(PYTEST) tests/ -v --tb=short

test-fast: ## Run all tests without coverage (faster)
	TESTING=$(TESTING) $(PYTEST) tests/ -v --tb=short --no-cov

test-unit: ## Run unit tests only (no-cov)
	TESTING=$(TESTING) $(PYTEST) tests/ -v -m unit --no-cov

test-integration: ## Run integration tests only (no-cov)
	TESTING=$(TESTING) $(PYTEST) tests/ -v -m integration --no-cov

test-cov: ## Run tests with full HTML + terminal coverage report
	TESTING=$(TESTING) $(PYTEST) tests/ --cov=src --cov-report=html --cov-report=term-missing

lint: ## Run linter
	ruff check src/ tests/
	mypy src/

format: ## Format code
	ruff format src/ tests/
	ruff check --fix src/ tests/

serve: run  ## Alias for run

run: ## Run the application locally (with hot-reload)
	$(PYTHON) -m uvicorn src.api.rest.app:app --host 0.0.0.0 --port 8000 --reload

migrate-init: ## Initialize alembic
	alembic init migrations

migrate-create: ## Create a new migration (usage: make migrate-create MSG="description")
	alembic revision --autogenerate -m "$(MSG)"

migrate-up: ## Apply all migrations
	alembic upgrade head

migrate-down: ## Rollback last migration
	alembic downgrade -1

docker-up: ## Start all services with Docker Compose
	docker compose up -d --build

docker-down: ## Stop all services
	docker compose down

docker-logs: ## View logs
	docker compose logs -f app

clean: ## Clean build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf dist/ build/ *.egg-info
