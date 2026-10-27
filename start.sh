#!/usr/bin/env bash
#
# Agent Fleet Explorer startup script.
#
# Usage:
#   ./start.sh            # Standard standalone mode: serves backend & Web UI at http://127.0.0.1:8000
#   ./start.sh --dev      # Dev mode: backend on :8000 (reload) + Vite on :5173
#   ./start.sh --open     # Launch standalone and open in browser
#   ./start.sh --sync     # Sync inventory before starting
#   ./start.sh --help     # Show all options

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# Text styles
BOLD='\033[1m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

info() { echo -e "${CYAN}==>${NC} ${BOLD}$1${NC}"; }
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}WARNING:${NC} $1"; }
fail() { echo -e "${RED}ERROR:${NC} $1" >&2; exit 1; }

# Frontend bind address (dev mode). Prefer the Tailscale interface so the UI is
# reachable over the tailnet without being exposed to the LAN; fall back to
# loopback when Tailscale is not installed or logged in.
detect_frontend_host() {
  local ts_ip=""
  if command -v tailscale >/dev/null 2>&1; then
    ts_ip=$(tailscale ip -4 2>/dev/null | head -n 1 || true)
  fi
  if [ -n "$ts_ip" ]; then
    printf '%s' "$ts_ip"
  else
    printf '127.0.0.1'
  fi
}

# Default arguments
HOST="127.0.0.1"
PORT="8000"
FRONTEND_PORT="5173"
FRONTEND_HOST="${FRONTEND_HOST:-$(detect_frontend_host)}"
AUTO_FRONTEND_PORT=true
DEV_MODE=false
OPEN_BROWSER=false
DO_SYNC=false
FORCE_BUILD=false

print_help() {
  cat <<EOF
Agent Fleet Explorer Launcher

Usage:
  ./start.sh [options]

Options:
  -d, --dev                   Run in development mode (FastAPI on :8000, Vite dev server on :5173)
  -o, --open                  Open the web UI in your default browser once started
  -b, --build                 Force rebuild frontend (web/dist) before starting
  -s, --sync                  Synchronize inventory before starting
  -h, --host <ip>             Host to bind backend server (default: 127.0.0.1)
  -p, --port <port>           Port for backend server (default: 8000)
  -fp, --frontend-port <port> Port for Vite dev server (dev mode only, default: 5173 or next free)
  --frontend-host <host>      Interface the Vite dev server binds to (default: Tailscale IP,
                              or 127.0.0.1 without Tailscale; use 0.0.0.0 for LAN too)
  --help                      Show this help message

Examples:
  ./start.sh                  # Standard launch (http://127.0.0.1:8000, no Vite/5173 needed)
  ./start.sh --open           # Launch and open browser
  ./start.sh --dev            # Concurrent dev mode with auto-port detection
  ./start.sh --dev -p 8080    # Dev mode on custom backend port
EOF
  exit 0
}

# Parse options
while [[ $# -gt 0 ]]; do
  case "$1" in
    -d|--dev)
      DEV_MODE=true
      shift
      ;;
    -o|--open)
      OPEN_BROWSER=true
      shift
      ;;
    -b|--build)
      FORCE_BUILD=true
      shift
      ;;
    -s|--sync)
      DO_SYNC=true
      shift
      ;;
    -h|--host)
      HOST="$2"
      shift 2
      ;;
    -p|--port)
      PORT="$2"
      shift 2
      ;;
    -fp|--frontend-port)
      FRONTEND_PORT="$2"
      AUTO_FRONTEND_PORT=false
      shift 2
      ;;
    --frontend-host)
      FRONTEND_HOST="$2"
      shift 2
      ;;
    --help)
      print_help
      ;;
    *)
      fail "Unknown argument: $1 (run with --help for usage)"
      ;;
  esac
done

# -----------------------------------------------------------------------------
# 1. Python & Virtual Environment Setup
# -----------------------------------------------------------------------------
VENV_DIR="$REPO_ROOT/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"

# Locate uv if available
UV_CMD=""
if command -v uv >/dev/null 2>&1; then
  UV_CMD="uv"
elif [ -x "$HOME/.local/bin/uv" ]; then
  UV_CMD="$HOME/.local/bin/uv"
fi

if [ ! -x "$VENV_PYTHON" ]; then
  info "Virtual environment not found. Setting up .venv..."
  if [ -n "$UV_CMD" ]; then
    "$UV_CMD" venv "$VENV_DIR" --python 3.13 2>/dev/null || "$UV_CMD" venv "$VENV_DIR"
    ok "Created .venv with uv"
    info "Installing Python dependencies..."
    "$UV_CMD" pip install -r backend/requirements.txt
    ok "Installed dependencies with uv"
  else
    PYTHON_BIN=""
    for py in python3.13 python3.12 python3.11 python3; do
      if command -v "$py" >/dev/null 2>&1; then
        PYTHON_BIN="$py"
        break
      fi
    done
    [ -z "$PYTHON_BIN" ] && fail "No python3 executable found. Please install Python 3.13 or uv."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    ok "Created .venv with $PYTHON_BIN"
    info "Installing Python dependencies with pip..."
    "$VENV_PYTHON" -m pip install --upgrade pip
    "$VENV_PYTHON" -m pip install -r backend/requirements.txt
    ok "Installed dependencies with pip"
  fi
fi

# Ensure PYTHONPATH sees backend
export PYTHONPATH="$REPO_ROOT/backend:${PYTHONPATH:-}"

# Optional: Run sync if requested
if [ "$DO_SYNC" = true ]; then
  info "Syncing fleet inventory..."
  "$VENV_PYTHON" -m agent_fleet.cli sync
  ok "Inventory synced"
fi

# -----------------------------------------------------------------------------
# 2. Frontend Assets Verification & Build
# -----------------------------------------------------------------------------
WEB_DIR="$REPO_ROOT/web"
DIST_DIR="$WEB_DIR/dist"

if [ "$DEV_MODE" = false ]; then
  # In standalone mode, backend serves dist/ directly
  if [ ! -d "$DIST_DIR" ] || [ "$FORCE_BUILD" = true ]; then
    info "Building frontend web UI..."
    if [ ! -d "$WEB_DIR/node_modules" ]; then
      info "Installing frontend dependencies (npm install)..."
      (cd "$WEB_DIR" && npm install)
    fi
    (cd "$WEB_DIR" && npm run build)
    ok "Frontend built successfully to web/dist"
  fi
else
  # In dev mode, ensure node_modules are present
  if [ ! -d "$WEB_DIR/node_modules" ]; then
    info "Installing frontend dependencies (npm install)..."
    (cd "$WEB_DIR" && npm install)
  fi
fi

# -----------------------------------------------------------------------------
# 3. Port Conflict Detection & Launch Services
# -----------------------------------------------------------------------------
is_port_in_use() {
  local port="$1"
  "$VENV_PYTHON" -c "
import socket, sys
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    try:
        s.bind(('127.0.0.1', int(sys.argv[1])))
        sys.exit(1) # free
    except OSError:
        sys.exit(0) # occupied
" "$port" 2>/dev/null
}

check_backend_port() {
  local port="$1"
  if is_port_in_use "$port"; then
    local pids=""
    local proc_info=""
    if command -v lsof >/dev/null 2>&1; then
      pids=$(lsof -ti :"$port" -sTCP:LISTEN 2>/dev/null || true)
    fi
    if [ -n "$pids" ]; then
      proc_info=$(ps -ww -o pid,user,lstart,command -p $pids 2>/dev/null | tail -n +2 || true)
    fi

    warn "Backend port $port is already in use."
    if [ -n "$proc_info" ]; then
      echo -e "      ${BOLD}Occupying process:${NC}"
      while IFS= read -r line; do
        [ -n "$line" ] && echo -e "        ${YELLOW}$line${NC}"
      done <<< "$proc_info"

      if echo "$proc_info" | grep -q "agent_fleet"; then
        echo -e "        ${CYAN}ℹ Note: This is an existing instance of agent-fleet-webui.${NC}"
      fi
    fi
    echo ""
    echo -e "      To stop the existing process: ${BOLD}kill $pids${NC}"
    echo -e "      Or run on a different port:   ${BOLD}./start.sh --port $((port + 1))${NC}"
    echo ""

    if [ -n "$pids" ] && [ -t 0 ]; then
      read -r -p "Stop existing process ($pids) and continue? [y/N] " ans
      if [[ "$ans" =~ ^[Yy]$ ]]; then
        kill $pids 2>/dev/null || true
        sleep 0.8
        if is_port_in_use "$port"; then
          kill -9 $pids 2>/dev/null || true
          sleep 0.5
        fi
        ok "Stopped process on port $port"
        return 0
      fi
    fi
    exit 1
  fi
}

check_backend_port "$PORT"

# In Dev mode, check and resolve Vite dev server port
if [ "$DEV_MODE" = true ]; then
  if is_port_in_use "$FRONTEND_PORT"; then
    if [ "$AUTO_FRONTEND_PORT" = true ]; then
      ORIG_PORT="$FRONTEND_PORT"
      while is_port_in_use "$FRONTEND_PORT"; do
        FRONTEND_PORT=$((FRONTEND_PORT + 1))
      done
      warn "Vite default port $ORIG_PORT is in use by another npm/dev project."
      ok "Automatically using next free port: ${BOLD}$FRONTEND_PORT${NC}"
    else
      fail "Frontend port $FRONTEND_PORT is already in use. Specify a different port with --frontend-port <port>."
    fi
  fi
fi

cleanup() {
  if [ -n "${BG_PIDS:-}" ]; then
    echo ""
    info "Stopping background services..."
    kill $BG_PIDS 2>/dev/null || true
    wait $BG_PIDS 2>/dev/null || true
  fi
}

if [ "$DEV_MODE" = true ]; then
  info "Starting in DEVELOPMENT mode..."
  echo -e "  Backend API:     ${BOLD}http://$HOST:$PORT${NC} (auto-reload on)"
  echo -e "  Vite Dev Server: ${BOLD}${GREEN}http://localhost:$FRONTEND_PORT${NC}"
  case "$FRONTEND_HOST" in
    127.0.0.1|localhost)
      ;;
    0.0.0.0|::|"")
      # All interfaces: the port is reachable from LAN and tailnet alike.
      if command -v tailscale >/dev/null 2>&1; then
        ts_ip=$(tailscale ip -4 2>/dev/null | head -n 1 || true)
        [ -n "$ts_ip" ] && echo -e "  Tailscale:       ${BOLD}${GREEN}http://$ts_ip:$FRONTEND_PORT${NC}"
      fi
      if command -v ipconfig >/dev/null 2>&1; then
        lan_ip=$(ipconfig getifaddr en0 2>/dev/null || true)
      else
        lan_ip=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
      fi
      [ -n "${lan_ip:-}" ] && echo -e "  LAN:             ${BOLD}${YELLOW}http://$lan_ip:$FRONTEND_PORT${NC}"
      ;;
    *)
      echo -e "  Network:         ${BOLD}${GREEN}http://$FRONTEND_HOST:$FRONTEND_PORT${NC}"
      ts_ip=""
      if command -v tailscale >/dev/null 2>&1; then
        ts_ip=$(tailscale ip -4 2>/dev/null | head -n 1 || true)
      fi
      if [ -n "$ts_ip" ] && [ "$ts_ip" = "$FRONTEND_HOST" ]; then
        ts_dns=$(tailscale status --json 2>/dev/null \
          | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)
        [ -n "$ts_dns" ] && echo -e "  MagicDNS:        ${BOLD}${GREEN}http://$ts_dns:$FRONTEND_PORT${NC}"
      fi
      ;;
  esac
  echo ""

  trap cleanup EXIT INT TERM
  BG_PIDS=""

  # Start backend
  "$VENV_PYTHON" -m uvicorn agent_fleet.main:app --host "$HOST" --port "$PORT" --reload &
  BG_PIDS="$! $BG_PIDS"

  # Start vite with configured ports
  (cd "$WEB_DIR" && FRONTEND_PORT="$FRONTEND_PORT" FRONTEND_HOST="$FRONTEND_HOST" BACKEND_PORT="$PORT" BACKEND_HOST="$HOST" npm run dev) &
  BG_PIDS="$! $BG_PIDS"

  if [ "$OPEN_BROWSER" = true ]; then
    (sleep 1.5 && (open "http://localhost:$FRONTEND_PORT" 2>/dev/null || xdg-open "http://localhost:$FRONTEND_PORT" 2>/dev/null || true)) &
  fi

  # Wait for background jobs
  wait
else
  info "Starting Agent Fleet Explorer..."
  echo -e "  Web Interface: ${BOLD}${GREEN}http://$HOST:$PORT${NC}"
  echo ""

  if [ "$OPEN_BROWSER" = true ]; then
    (sleep 1.2 && (open "http://$HOST:$PORT" 2>/dev/null || xdg-open "http://$HOST:$PORT" 2>/dev/null || true)) &
  fi

  exec "$VENV_PYTHON" -m uvicorn agent_fleet.main:app --host "$HOST" --port "$PORT"
fi
