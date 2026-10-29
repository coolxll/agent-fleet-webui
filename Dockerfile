# ==========================================
# Stage 1: Build Frontend Assets
# ==========================================
FROM node:22-slim AS frontend-builder

WORKDIR /app/web

# Install frontend dependencies (utilize layer caching, deterministic install)
COPY web/package.json web/package-lock.json* ./
RUN npm ci

# Build frontend production bundle
COPY web/ ./
RUN npm run build

# ==========================================
# Stage 2: Production Runtime
# ==========================================
FROM python:3.13-slim AS runner

# Install OpenSSH client (essential for fleet controller remote SSH probes and handoff)
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python backend dependencies
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copy backend source code
COPY backend/ /app/backend/

# Copy built frontend assets from frontend-builder stage
COPY --from=frontend-builder /app/web/dist /app/web/dist

# Copy example templates and default inventory fallback (guarantees clean checkout builds)
COPY examples/ /app/examples/
COPY examples/inventory.yaml /app/inventory.yaml

# Version and build metadata arguments
ARG AGENT_FLEET_VERSION=0.2.0
ARG AGENT_FLEET_COMMIT=dev
ARG BUILD_TIME=""

# Environment defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    AGENT_FLEET_HOST=0.0.0.0 \
    AGENT_FLEET_PORT=8000 \
    AGENT_FLEET_DATA_DIR=/app/data \
    AGENT_FLEET_VERSION=${AGENT_FLEET_VERSION} \
    AGENT_FLEET_COMMIT_SHA=${AGENT_FLEET_COMMIT} \
    AGENT_FLEET_BUILD_TIME=${BUILD_TIME}

# Populate version discovery metadata across runtime candidate paths
RUN python -c "import json, os; \
v = os.environ.get('AGENT_FLEET_VERSION') or '0.2.0'; \
c = os.environ.get('AGENT_FLEET_COMMIT_SHA') or 'dev'; \
bt = os.environ.get('AGENT_FLEET_BUILD_TIME') or ''; \
rel = not v.endswith('-dev'); \
data = {'version': v, 'commit': c, 'commit_short': c[:7] if c != 'dev' else 'dev', 'build_time': bt or None, 'is_release': rel}; \
os.makedirs('/app/data', exist_ok=True); \
[open(p, 'w', encoding='utf-8').write(json.dumps(data, indent=2) + '\n') for p in ['/app/backend/agent_fleet/version.json', '/app/version.json', '/app/data/version.json']]"

# Expose WebUI and API service port
EXPOSE 8000

# Volume for persisted database and configuration
VOLUME ["/app/data"]

CMD ["uvicorn", "agent_fleet.main:app", "--host", "0.0.0.0", "--port", "8000"]
