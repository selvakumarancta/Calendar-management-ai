# =============================================================================
# Calendar Agent — Multi-stage Production Dockerfile
# =============================================================================

# --- Stage 1: Builder ---
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# --- Stage 2: Runtime ---
FROM python:3.12-slim AS runtime

WORKDIR /app

# Security: run as non-root
RUN groupadd -r agent && useradd -r -g agent agent

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY src/ ./src/
COPY static/ ./static/
COPY alembic.ini ./
COPY migrations/ ./migrations/
COPY railway.sh ./

# Create data directory
RUN mkdir -p /app/data && chown -R agent:agent /app && chmod +x /app/railway.sh

USER agent

EXPOSE 8000

CMD ["bash", "railway.sh"]
