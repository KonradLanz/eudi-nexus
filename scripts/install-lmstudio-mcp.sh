#!/usr/bin/env bash
# =============================================================================
# install-lmstudio-mcp.sh
# Registers eudi-nexus as an MCP server in LM Studio (macOS).
#
# LM Studio ≥0.3.x uses a single mcp.json (Claude Desktop format):
#   ~/Library/Application Support/LM-Studio/mcp.json
#   { "mcpServers": { "eudi-nexus": { ... } } }
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

# LM Studio 0.3.x: single mcp.json (same format as Claude Desktop)
LMS_CONFIG_FILE="${HOME}/Library/Application Support/LM-Studio/mcp.json"

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

# ── Helper: merge server entry into mcp.json ─────────────────────────────
# Requires python3 (available since we already check for it below)
merge_server() {
  local key="$1" value="$2" file="$3"
  python3 - <<PYEOF
import json, pathlib, sys
p = pathlib.Path("$file")
cfg = json.loads(p.read_text()) if p.exists() else {}
cfg.setdefault("mcpServers", {})
cfg["mcpServers"]["$key"] = $value
p.write_text(json.dumps(cfg, indent=2))
print(json.dumps(cfg, indent=2))
PYEOF
}

remove_server() {
  local key="$1" file="$2"
  python3 - <<PYEOF
import json, pathlib
p = pathlib.Path("$file")
if not p.exists():
    print("File not found, nothing to do.")
    exit(0)
cfg = json.loads(p.read_text())
removed = cfg.get("mcpServers", {}).pop("$key", None)
if removed:
    p.write_text(json.dumps(cfg, indent=2))
    print("Removed '$key' from mcpServers.")
else:
    print("'$key' not found in mcpServers, nothing to do.")
PYEOF
}

# ── Remove mode ──────────────────────────────────────────────────────────
if $REMOVE; then
  echo ""
  result=$(remove_server "$SERVER_NAME" "$LMS_CONFIG_FILE")
  ok "$result"
  warn "Restart LM Studio to apply."
  exit 0
fi

# ── Pre-flight checks ──────────────────────────────────────────────────────
echo ""
echo -e "eudi-nexus → LM Studio MCP registration"
echo -e "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check: repo root
[[ -f "${REPO_ROOT}/pytest.ini" ]] || fail "Cannot find repo root (expected pytest.ini at ${REPO_ROOT})"
ok "Repo root:  ${REPO_ROOT}"

# Check: venv python
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3 || true)"
  [[ -x "$PYTHON" ]] || fail ".venv/bin/python3 not found and no system python3."
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
  info "(Registration continues — configure LM Studio now, build DB later)"
else
  DB_SIZE=$(du -sh "$DB_PATH" | cut -f1)
  ok "Database:   ${DB_PATH}  (${DB_SIZE})"
fi

# Check: fastmcp + sqlite_vec
if ! "$PYTHON" -c "import fastmcp" 2>/dev/null; then
  warn "fastmcp not installed — run:  $PYTHON -m pip install fastmcp sqlite-vec httpx"
else
  ok "fastmcp:    installed"
fi
if ! "$PYTHON" -c "import sqlite_vec" 2>/dev/null; then
  warn "sqlite_vec not installed — run:  $PYTHON -m pip install sqlite-vec"
else
  ok "sqlite_vec: installed"
fi

# ── Build server JSON block ──────────────────────────────────────────────────
SERVER_JSON=$(cat <<EOF
{
  "command": "${PYTHON}",
  "args": ["${SCRIPT}"],
  "env": { "MCP_DB_PATH": "${DB_PATH}" }
}
EOF
)

echo ""
echo -e "Config file: ${LMS_CONFIG_FILE}"
echo -e "Entry to merge:"
echo "  \"${SERVER_NAME}\": ${SERVER_JSON}"
echo ""

if $DRY_RUN; then
  warn "Dry-run: nothing written."
  exit 0
fi

# ── Merge into mcp.json ─────────────────────────────────────────────────────
mkdir -p "$(dirname "$LMS_CONFIG_FILE")"
merge_server "$SERVER_NAME" "$SERVER_JSON" "$LMS_CONFIG_FILE" > /dev/null
ok "Written:    ${LMS_CONFIG_FILE}"

# Show final state of mcpServers
echo ""
info "Current mcpServers in mcp.json:"
python3 -c "
import json, pathlib
cfg = json.loads(pathlib.Path('${LMS_CONFIG_FILE}').read_text())
for k in cfg.get('mcpServers', {}):
    print(f'    • {k}')
"

# ── Smoke-test ───────────────────────────────────────────────────────────────
echo ""
echo -e "Smoke-test: starting MCP server for 2 s …"
if MCP_DB_PATH="$DB_PATH" timeout 2 "$PYTHON" "$SCRIPT" >/dev/null 2>&1; then
  ok "Server started cleanly."
elif [[ $? -eq 124 ]]; then
  ok "Server running (killed after 2 s timeout — expected for stdio MCP)."
else
  warn "Server exited with an error. Check manually:"
  info "MCP_DB_PATH=${DB_PATH} ${PYTHON} ${SCRIPT}"
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
ok "Done! Restart LM Studio to load the eudi-nexus MCP server."
echo ""
info "Tools available after restart:"
info "  • search_norm   — hybrid BM25 + cosine search"
info "  • get_segment   — fetch segment by ID"
info "  • get_section   — all segments in a section"
info "  • list_norms    — list all indexed norms"
echo ""
info "To undo:  bash scripts/install-lmstudio-mcp.sh --remove"
echo ""
