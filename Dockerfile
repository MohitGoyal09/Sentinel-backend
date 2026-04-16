# Stage 1: Build dependencies
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock* requirements.txt ./

# Install dependencies using requirements.txt (pinned versions for reproducibility)
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Create non-root user
RUN groupadd --gid 1000 sentinel \
    && useradd --uid 1000 --gid sentinel --shell /bin/bash --create-home sentinel

# Copy application code
COPY --chown=sentinel:sentinel . .

USER sentinel

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=5 \
    CMD curl -f http://localhost:80/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
