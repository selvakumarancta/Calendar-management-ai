.PHONY: help install dev test lint format run migrate docker-up docker-down clean

PYTHON := python
PIP := pip

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install production dependencies
	$(PIP) install -e .

dev: ## Install development dependencies
	$(PIP) install -e ".[dev]"
	pre-commit install

test: ## Run all tests
	pytest tests/ -v --tb=short

test-unit: ## Run unit tests only
	pytest tests/ -v -m unit

test-integration: ## Run integration tests only
	pytest tests/ -v -m integration

test-cov: ## Run tests with coverage
	pytest tests/ --cov=src --cov-report=html --cov-report=term-missing

lint: ## Run linter
	ruff check src/ tests/
	mypy src/

format: ## Format code
	ruff format src/ tests/
	ruff check --fix src/ tests/

run: ## Run the application locally
	uvicorn src.api.rest.app:create_app --factory --host 0.0.0.0 --port 8000 --reload

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
