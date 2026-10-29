# ==========================================
# Stage 1: Build Frontend Assets
# ==========================================
FROM node:20-slim AS frontend-builder

WORKDIR /app/web

# Install frontend dependencies (utilize layer caching)
COPY web/package.json web/package-lock.json* ./
RUN npm ci || npm install

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

# Copy default inventory template if present
COPY inventory.yaml* /app/

# Environment defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    AGENT_FLEET_HOST=0.0.0.0 \
    AGENT_FLEET_PORT=8000 \
    AGENT_FLEET_DATA_DIR=/app/data

# Expose WebUI and API service port
EXPOSE 8000

# Volume for persisted database and configuration
VOLUME ["/app/data"]

CMD ["uvicorn", "agent_fleet.main:app", "--host", "0.0.0.0", "--port", "8000"]
