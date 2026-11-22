# =============================================================================
# M&A Due Diligence Copilot — Multi-Stage Non-Root Production Container
# Supports both Streamlit Investment Committee UI (port 8501) and
# FastAPI Enterprise REST Backend (port 8000).
# =============================================================================

# ---- Stage 1: Dependency Builder ----
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ---- Stage 2: Minimal Hardened Non-Root Runtime ----
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="M&A Due Diligence Copilot" \
      org.opencontainers.image.description="Institutional M&A Intelligence, Deterministic Valuation & Boardroom Publishing Platform" \
      org.opencontainers.image.vendor="JakobFerschin"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    API_PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 appgroup \
    && useradd --uid 10001 --gid appgroup --shell /sbin/nologin --create-home appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=appuser:appgroup . /app

RUN mkdir -p /app/data/uploads /app/data/outputs/deal_room \
    && chown -R appuser:appgroup /app/data

USER appuser

EXPOSE 8501 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8501/_stcore/health || curl -fsS http://127.0.0.1:8000/health || exit 1

# Default entrypoint launches the Streamlit Investment Committee UI.
# Override CMD with `uvicorn api.main:app --host 0.0.0.0 --port 8000` for the FastAPI service.
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
