# ── Stage 1: build wheels ────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .

# CPU-only torch keeps the image ~2.5GB instead of ~7GB.
# We never train here; embedding + reranking on CPU is fine at
# this scale and removes the GPU dependency entirely.
RUN pip install --upgrade pip \
 && pip wheel --wheel-dir /wheels \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      -r requirements.txt


# ── Stage 2: build the UI ────────────────────────────────────
# npm ci installs exactly what package-lock.json pins, so the UI
# builds the same way every time — no surprise upgrade the day
# before a demo. Only the static output is carried forward.
FROM node:22-slim AS ui

WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build


# ── Stage 3: runtime ─────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    PYTHONPATH=/app/src

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl \
 && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd --create-home --uid 1000 copilot

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
 && rm -rf /wheels

COPY --chown=copilot:copilot src/ ./src/
COPY --chown=copilot:copilot config/ ./config/
COPY --chown=copilot:copilot scripts/ ./scripts/
# The built UI; the API serves it at / (same origin, no CORS needed).
COPY --from=ui --chown=copilot:copilot /ui/dist ./ui/dist

RUN mkdir -p /app/.cache/huggingface && chown -R copilot:copilot /app/.cache

USER copilot

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "ops_copilot.main:app", "--host", "0.0.0.0", "--port", "8000"]
