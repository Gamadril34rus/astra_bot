# ASTRA BOT — Dockerfile
# Production-ready container for the trading bot

FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_HOME=/app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    curl \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -s /bin/bash -u 1000 botuser

# Set working directory
WORKDIR ${APP_HOME}

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir lightgbm xgboost scikit-learn pandas numpy

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p \
    ${APP_HOME}/config \
    ${APP_HOME}/data \
    ${APP_HOME}/logs \
    ${APP_HOME}/models \
    ${APP_HOME}/backups \
    && chown -R botuser:botuser ${APP_HOME}

# Switch to non-root user
USER botuser

# Expose metrics port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import astra_bot; print('ASTRA BOT OK')" || exit 1

# Default command
CMD ["python", "-m", "astra_bot.main"]
