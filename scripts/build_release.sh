#!/usr/bin/env bash
#
# Release artifact build and packaging script for Agent Fleet WebUI.
#
# Usage:
#   ./scripts/build_release.sh <VERSION_TAG> [OPTIONS]
#   ./scripts/build_release.sh v0.2.0
#   ./scripts/build_release.sh v0.2.0 --dry-run
#
# Options:
#   -d, --dry-run               Build and verify standalone archive, but clean working tree
#   -o, --output-dir <DIR>      Directory where release artifacts are placed (default: dist)
#   --skip-frontend-build       Skip 'npm run build' (assumes web/dist already exists)
#   --skip-verification         Skip standalone archive execution verification
#   --clean                     Clean up backend/agent_fleet/version.json after packaging
#   -h, --help                  Show this help message

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Text styles matching start.sh
BOLD='\033[1m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

info() { echo -e "${CYAN}==>${NC} ${BOLD}$1${NC}"; }
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}WARNING:${NC} $1"; }
fail() { echo -e "${RED}ERROR:${NC} $1" >&2; exit 1; }

compute_sha256() {
  local target="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$target"
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$target"
  else
    python3 -c "import hashlib, sys; f=open(sys.argv[1],'rb'); print(f'{hashlib.sha256(f.read()).hexdigest()}  {sys.argv[1]}')" "$target"
  fi
}

VERSION_TAG=""
DRY_RUN=false
OUTPUT_DIR="$REPO_ROOT/dist"
SKIP_FRONTEND=false
SKIP_VERIFICATION=false
CLEAN_WORKING_TREE=false

print_help() {
  cat <<EOF
Agent Fleet Release Packager

Usage:
  ./scripts/build_release.sh [OPTIONS] <VERSION_TAG>

Arguments:
  <VERSION_TAG>               Release version tag (e.g. v0.2.0 or 0.2.0)

Options:
  -d, --dry-run               Dry run: package & verify standalone, then remove version.json
  -o, --output-dir <DIR>      Directory to store release tarball & checksums (default: ./dist)
  --skip-frontend-build       Skip web/ build step (useful if web/dist is already built)
  --skip-verification         Skip unpacking and executing standalone verification
  --clean                     Clean up generated backend/agent_fleet/version.json
  -h, --help                  Show this help message

Examples:
  ./scripts/build_release.sh v0.2.0
  ./scripts/build_release.sh v0.2.0 --dry-run
  ./scripts/build_release.sh v0.2.0 --output-dir ./release_artifacts
EOF
  exit 0
}

# Parse options & positional args
POSITIONAL_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      print_help
      ;;
    -d|--dry-run)
      DRY_RUN=true
      CLEAN_WORKING_TREE=true
      shift
      ;;
    -o|--output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --skip-frontend-build)
      SKIP_FRONTEND=true
      shift
      ;;
    --skip-verification)
      SKIP_VERIFICATION=true
      shift
      ;;
    --clean)
      CLEAN_WORKING_TREE=true
      shift
      ;;
    *)
      POSITIONAL_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ ${#POSITIONAL_ARGS[@]} -eq 0 ]]; then
  fail "Missing required argument: <VERSION_TAG> (e.g. v0.2.0). Run with --help for details."
fi

VERSION_TAG="${POSITIONAL_ARGS[0]}"

# Normalize tag and semver
# Tag format expected: vX.Y.Z or X.Y.Z
if [[ "$VERSION_TAG" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+.*$ ]]; then
  if [[ "$VERSION_TAG" == v* ]]; then
    RELEASE_TAG="$VERSION_TAG"
    SEMVER="${VERSION_TAG#v}"
  else
    RELEASE_TAG="v$VERSION_TAG"
    SEMVER="$VERSION_TAG"
  fi
else
  warn "Version tag '$VERSION_TAG' does not follow standard semantic versioning (vX.Y.Z). Proceeding with raw values."
  RELEASE_TAG="$VERSION_TAG"
  SEMVER="${VERSION_TAG#v}"
fi

info "Starting Agent Fleet release build for tag: ${BOLD}${RELEASE_TAG}${NC} (semver: ${SEMVER})"

# -----------------------------------------------------------------------------
# 1. Discover Git Commit and Build Timestamp
# -----------------------------------------------------------------------------
COMMIT_SHA="dev"
COMMIT_SHORT="dev"
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  COMMIT_SHA="$(git rev-parse HEAD 2>/dev/null || echo "dev")"
  COMMIT_SHORT="$(git rev-parse --short=7 HEAD 2>/dev/null || echo "dev")"
fi
BUILD_TIME="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

info "Git Commit: ${COMMIT_SHA} (${COMMIT_SHORT}), Build Time: ${BUILD_TIME}"

# -----------------------------------------------------------------------------
# 2. Generate backend/agent_fleet/version.json
# -----------------------------------------------------------------------------
VERSION_FILE="$REPO_ROOT/backend/agent_fleet/version.json"
info "Generating release metadata in ${VERSION_FILE}..."

cat <<EOF > "$VERSION_FILE"
{
  "version": "${SEMVER}",
  "commit": "${COMMIT_SHA}",
  "commit_short": "${COMMIT_SHORT}",
  "build_time": "${BUILD_TIME}",
  "is_release": true
}
EOF

ok "Created version.json with release flag"

# -----------------------------------------------------------------------------
# 3. Build Frontend Assets (web/dist)
# -----------------------------------------------------------------------------
if [ "$SKIP_FRONTEND" = true ]; then
  info "Skipping frontend build (--skip-frontend-build requested)"
  if [ ! -d "$REPO_ROOT/web/dist" ] || [ ! -f "$REPO_ROOT/web/dist/index.html" ]; then
    fail "web/dist/index.html is missing. Cannot skip frontend build."
  fi
else
  info "Building frontend production bundle in web/..."
  if ! command -v npm >/dev/null 2>&1; then
    fail "npm is required to build frontend assets. Please install Node.js."
  fi
  (
    cd "$REPO_ROOT/web"
    npm run build
  )
  if [ ! -f "$REPO_ROOT/web/dist/index.html" ]; then
    fail "Frontend build completed but web/dist/index.html was not found."
  fi
  ok "Frontend build completed successfully"
fi

# -----------------------------------------------------------------------------
# 4. Assemble Distributable Release Package
# -----------------------------------------------------------------------------
PKG_NAME="agent-fleet-${RELEASE_TAG}"
STAGE_DIR="$(mktemp -d -t agent_fleet_stage.XXXXXX)"
TARGET_DIR="${STAGE_DIR}/${PKG_NAME}"

cleanup() {
  local exit_code=$?
  if [ -d "$STAGE_DIR" ]; then
    rm -rf "$STAGE_DIR"
  fi
  if [ "$CLEAN_WORKING_TREE" = true ] && [ -f "$VERSION_FILE" ]; then
    rm -f "$VERSION_FILE"
    ok "Cleaned up ${VERSION_FILE}"
  fi
  exit $exit_code
}
trap cleanup EXIT INT TERM

info "Assembling release directory structure in staging: ${TARGET_DIR}..."
mkdir -p "${TARGET_DIR}/backend"
mkdir -p "${TARGET_DIR}/web"

# Copy backend python package (excluding bytecache, databases and test caches)
rsync -a \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.pytest_cache' \
  --exclude '.coverage' \
  --exclude '.mypy_cache' \
  --exclude '*.db' \
  --exclude '*.db-wal' \
  --exclude '*.db-shm' \
  --exclude '*.db-journal' \
  --exclude '.DS_Store' \
  "$REPO_ROOT/backend/" "${TARGET_DIR}/backend/"

# Ensure version.json is in target package
cp "$VERSION_FILE" "${TARGET_DIR}/backend/agent_fleet/version.json"

# Copy web/dist
cp -R "$REPO_ROOT/web/dist" "${TARGET_DIR}/web/dist"

# Copy default configurations, scripts and manifests
for f in start.sh dev.sh Dockerfile docker-compose.yml .dockerignore .env.example README.md README_ZH.md inventory.yaml; do
  if [ -e "$REPO_ROOT/$f" ]; then
    cp -R "$REPO_ROOT/$f" "${TARGET_DIR}/"
  fi
done

# Copy scripts directory (management utilities)
if [ -d "$REPO_ROOT/scripts" ]; then
  mkdir -p "${TARGET_DIR}/scripts"
  for s in "$REPO_ROOT/scripts"/*; do
    if [ -f "$s" ]; then
      cp "$s" "${TARGET_DIR}/scripts/"
    fi
  done
fi

# Copy docs
if [ -d "$REPO_ROOT/docs" ]; then
  mkdir -p "${TARGET_DIR}/docs"
  cp -R "$REPO_ROOT/docs"/* "${TARGET_DIR}/docs/" 2>/dev/null || true
fi

# Ensure executable scripts have execution bit
chmod +x "${TARGET_DIR}/start.sh" 2>/dev/null || true
if [ -d "${TARGET_DIR}/scripts" ]; then
  chmod +x "${TARGET_DIR}/scripts"/*.sh 2>/dev/null || true
fi

ok "Staged release package files"

# -----------------------------------------------------------------------------
# 5. Create Archives (tar.gz and zip)
# -----------------------------------------------------------------------------
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
TAR_FILE="${OUTPUT_DIR}/${PKG_NAME}.tar.gz"
ZIP_FILE="${OUTPUT_DIR}/${PKG_NAME}.zip"

info "Compressing tarball: ${TAR_FILE}..."
tar -czf "$TAR_FILE" -C "$STAGE_DIR" "$PKG_NAME"
ok "Created $(basename "$TAR_FILE") ($(du -h "$TAR_FILE" | cut -f1))"

if command -v zip >/dev/null 2>&1; then
  info "Compressing zip archive: ${ZIP_FILE}..."
  (
    cd "$STAGE_DIR"
    zip -rq "$ZIP_FILE" "$PKG_NAME"
  )
  ok "Created $(basename "$ZIP_FILE") ($(du -h "$ZIP_FILE" | cut -f1))"
fi

# -----------------------------------------------------------------------------
# 6. Compute SHA-256 Checksums
# -----------------------------------------------------------------------------
SUMS_FILE="${OUTPUT_DIR}/SHA256SUMS"
info "Generating SHA-256 checksums in ${SUMS_FILE}..."
(
  cd "$OUTPUT_DIR"
  compute_sha256 "${PKG_NAME}.tar.gz" > "SHA256SUMS"
  if [ -f "${PKG_NAME}.zip" ]; then
    compute_sha256 "${PKG_NAME}.zip" >> "SHA256SUMS"
  fi
)
ok "Generated SHA256SUMS:"
cat "$SUMS_FILE" | sed 's/^/    /'

# -----------------------------------------------------------------------------
# 7. Standalone Package Verification
# -----------------------------------------------------------------------------
if [ "$SKIP_VERIFICATION" = true ]; then
  info "Skipping standalone verification (--skip-verification specified)"
else
  info "Verifying packaged standalone build from archive..."
  VERIFY_DIR="$(mktemp -d -t agent_fleet_verify.XXXXXX)"
  tar -xzf "$TAR_FILE" -C "$VERIFY_DIR"

  UNPACKED_ROOT="${VERIFY_DIR}/${PKG_NAME}"
  [ -f "${UNPACKED_ROOT}/backend/agent_fleet/version.json" ] || fail "Verification failed: version.json missing in archive"
  [ -f "${UNPACKED_ROOT}/web/dist/index.html" ] || fail "Verification failed: web/dist/index.html missing in archive"
  [ -f "${UNPACKED_ROOT}/backend/requirements.txt" ] || fail "Verification failed: backend/requirements.txt missing"

  # Test Python runtime discovery
  PYTHON_EXEC="python3"
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON_EXEC="$REPO_ROOT/.venv/bin/python"
  fi

  PYTHONPATH="${UNPACKED_ROOT}/backend" "$PYTHON_EXEC" -c "
import sys
from agent_fleet.version import get_version_info

info = get_version_info()
print('Standalone package version info:', info)
assert info['is_release'] is True, f'Expected is_release=True, got {info[\"is_release\"]}'
assert info['version'] in ('${SEMVER}', '${RELEASE_TAG}'), f'Version mismatch: {info[\"version\"]} vs ${SEMVER}'
assert info['commit'] == '${COMMIT_SHA}', f'Commit mismatch: {info[\"commit\"]} vs ${COMMIT_SHA}'
print('✓ Standalone metadata verification confirmed.')
"

  # Validate SHA256SUMS matches generated files
  (
    cd "$OUTPUT_DIR"
    if command -v sha256sum >/dev/null 2>&1; then
      sha256sum -c SHA256SUMS --ignore-missing >/dev/null 2>&1 || sha256sum -c SHA256SUMS >/dev/null 2>&1
    elif command -v shasum >/dev/null 2>&1; then
      shasum -a 256 -c SHA256SUMS >/dev/null 2>&1
    fi
  )
  ok "Checksum verification passed"

  rm -rf "$VERIFY_DIR"
  ok "Standalone archive verification passed successfully"
fi

echo ""
ok "============================================================"
ok " Release artifacts created successfully for ${BOLD}${RELEASE_TAG}${NC}:"
ok "   - Tarball:  ${TAR_FILE}"
if [ -f "$ZIP_FILE" ]; then
ok "   - Zip:      ${ZIP_FILE}"
fi
ok "   - Checksum: ${SUMS_FILE}"
ok "============================================================"

if [ "$DRY_RUN" = true ]; then
  info "Dry-run mode: build and verification completed without leaving persistent state."
fi
