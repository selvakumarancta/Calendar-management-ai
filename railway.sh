#!/bin/bash
set -e

echo "==> PORT=${PORT:-8000}"
echo "==> DATABASE_URL prefix: ${DATABASE_URL:0:30}..."

# Railway injects DATABASE_URL — SQLAlchemy async needs postgresql+asyncpg://
if [[ "$DATABASE_URL" == postgres://* ]]; then
  export DATABASE_URL="${DATABASE_URL/postgres:/postgresql+asyncpg:}"
elif [[ "$DATABASE_URL" == postgresql://* ]]; then
  export DATABASE_URL="${DATABASE_URL/postgresql:/postgresql+asyncpg:}"
fi

echo "==> Fixed DATABASE_URL prefix: ${DATABASE_URL:0:40}..."

echo "==> Running Alembic migrations..."
alembic upgrade head

echo "==> Starting Calendar Agent server on port ${PORT:-8000}..."
exec uvicorn src.api.rest.app:app --host 0.0.0.0 --port "${PORT:-8000}"
