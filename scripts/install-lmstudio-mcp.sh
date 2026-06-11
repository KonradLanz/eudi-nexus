#!/usr/bin/env bash
# =============================================================================
# install-lmstudio-mcp.sh
# Registers eudi-nexus as an MCP server in LM Studio (macOS).
#
# Usage:
#   bash scripts/install-lmstudio-mcp.sh
#   bash scripts/install-lmstudio-mcp.sh --dry-run   # preview only
#   bash scripts/install-lmstudio-mcp.sh --remove    # undo registration
# =============================================================================
set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────
SERVER_NAME="eudi-nexus"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="${REPO_ROOT}/scripts/mcp-server.py"
DB_PATH="${REPO_ROOT}/corpus/eudi-nexus.db"
PYTHON="${REPO_ROOT}/.venv/bin/python3"

# LM Studio MCP config file (macOS default path)
LMS_CONFIG_DIR="${HOME}/Library/Application Support/LM-Studio/mcp-servers"
LMS_CONFIG_FILE="${LMS_CONFIG_DIR}/${SERVER_NAME}.json"

# ── Args ────────────────────────────────────────────────────────────────
DRY_RUN=false
REMOVE=false

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --remove)  REMOVE=true ;;
    *) echo "Unknown flag: $arg" && exit 1 ;;
  esac
done

# ── Colors ──────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✔${NC}  $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
fail() { echo -e "${RED}✘${NC}  $*" >&2; exit 1; }
info() { echo -e "    $*"; }

# ── Remove mode ──────────────────────────────────────────────────────────
if $REMOVE; then
  if [[ -f "$LMS_CONFIG_FILE" ]]; then
    rm "$LMS_CONFIG_FILE"
    ok "Removed: ${LMS_CONFIG_FILE}"
    warn "Restart LM Studio to apply."
  else
    warn "Config not found, nothing to remove: ${LMS_CONFIG_FILE}"
  fi
  exit 0
fi

# ── Pre-flight checks ──────────────────────────────────────────────────────
echo ""
echo -e "eudi-nexus → LM Studio MCP registration"
echo -e "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check: repo root
if [[ ! -f "${REPO_ROOT}/pytest.ini" ]]; then
  fail "Cannot find repo root (expected pytest.ini at ${REPO_ROOT})"
fi
ok "Repo root:  ${REPO_ROOT}"

# Check: venv python
if [[ ! -x "$PYTHON" ]]; then
  # fallback: system python3
  PYTHON="$(command -v python3 || true)"
  [[ -x "$PYTHON" ]] || fail ".venv/bin/python3 not found and no system python3. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  warn "Using system python3: ${PYTHON}  (tip: activate .venv for isolation)"
else
  ok "Python:     ${PYTHON}"
fi

# Check: mcp-server.py
[[ -f "$SCRIPT" ]] || fail "mcp-server.py not found at: ${SCRIPT}"
ok "Server:     ${SCRIPT}"

# Check: DB
if [[ ! -f "$DB_PATH" ]]; then
  warn "DB not found: ${DB_PATH}"
  info "Build it first:  python scripts/build-index.py"
  info "(Registration continues so you can configure LM Studio now)"
else
  DB_SIZE=$(du -sh "$DB_PATH" | cut -f1)
  ok "Database:   ${DB_PATH}  (${DB_SIZE})"
fi

# Check: fastmcp installed in chosen python
if ! "$PYTHON" -c "import fastmcp" 2>/dev/null; then
  warn "fastmcp not importable with: ${PYTHON}"
  info "Run:  $PYTHON -m pip install fastmcp sqlite-vec httpx"
else
  ok "fastmcp:    installed"
fi

# Check: sqlite_vec installed
if ! "$PYTHON" -c "import sqlite_vec" 2>/dev/null; then
  warn "sqlite_vec not importable with: ${PYTHON}"
  info "Run:  $PYTHON -m pip install sqlite-vec"
else
  ok "sqlite_vec: installed"
fi

# ── Build JSON config ────────────────────────────────────────────────────────
CONFIG=$(cat <<EOF
{
  "name": "${SERVER_NAME}",
  "transport": "stdio",
  "command": "${PYTHON}",
  "args": ["${SCRIPT}"],
  "env": {
    "MCP_DB_PATH": "${DB_PATH}"
  }
}
EOF
)

echo ""
echo -e "Config to write → ${LMS_CONFIG_FILE}"
echo "$CONFIG"
echo ""

if $DRY_RUN; then
  warn "Dry-run: nothing written."
  exit 0
fi

# ── Write config ──────────────────────────────────────────────────────────────
mkdir -p "$LMS_CONFIG_DIR"
echo "$CONFIG" > "$LMS_CONFIG_FILE"

ok "Written:    ${LMS_CONFIG_FILE}"

# ── Quick smoke-test: can the server start and exit cleanly? ────────────────
echo ""
echo -e "Smoke-test: starting MCP server for 2 s …"
if MCP_DB_PATH="$DB_PATH" timeout 2 "$PYTHON" "$SCRIPT" >/dev/null 2>&1; then
  ok "Server started cleanly (exited on its own — no stdin)."
elif [[ $? -eq 124 ]]; then
  # timeout = server is running and waiting for MCP input — that's correct
  ok "Server is running (killed after 2 s timeout — expected for stdio MCP)."
else
  warn "Server exited with an error. Check manually:"
  info "MCP_DB_PATH=${DB_PATH} ${PYTHON} ${SCRIPT}"
fi

# ── Done ───────────────────────────────────────────────────────────────────
echo ""
ok "Done! Restart LM Studio to load the eudi-nexus MCP server."
echo ""
info "Tools available after restart:"
info "  • search_norm   — hybrid BM25 + cosine search"
info "  • get_segment   — fetch segment by ID"
info "  • get_section   — all segments in a section"
info "  • list_norms    — list all indexed norms"
info ""
info "To undo: bash scripts/install-lmstudio-mcp.sh --remove"
echo ""
