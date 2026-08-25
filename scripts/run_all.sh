#!/usr/bin/env bash
# ============================================================================
# Configurable Document Extraction — one-command setup & run
#
#   ./scripts/run_all.sh
#
# Does EVERYTHING needed to install and run the project from scratch:
#   1. Verifies prerequisites (Python 3.10+, Node 18+)
#   2. Creates the Python virtualenv (.venv) and installs API dependencies
#   3. Creates api/.env and web/.env from their .env.example templates
#   4. Installs web dependencies (npm)
#   5. Starts the API (:8000) and the Web UI (:5173)
#
# Ctrl+C stops both servers. Re-running skips everything already installed.
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

step() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Prerequisites
# ---------------------------------------------------------------------------
step "Checking prerequisites"

command -v python3 >/dev/null 2>&1 || fail "python3 not found. Install Python 3.10+ first (https://python.org)."
PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info[0])')
PY_MINOR=$(python3 -c 'import sys; print(sys.version_info[1])')
[ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -ge 10 ] || fail "Python 3.10+ required, found $(python3 -V 2>&1)."
echo "    python3 ......... $(python3 -V 2>&1) ✓"

command -v node >/dev/null 2>&1 || fail "node not found. Install Node.js 18+ first (https://nodejs.org)."
NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
[ "$NODE_MAJOR" -ge 18 ] || fail "Node.js 18+ required, found $(node -v)."
echo "    node ............ $(node -v) ✓"

# ---------------------------------------------------------------------------
# 2. Python virtualenv + API dependencies
# ---------------------------------------------------------------------------
step "Setting up Python environment (.venv)"

if [ ! -d "$ROOT/.venv" ]; then
    echo "    creating virtualenv..."
    python3 -m venv "$ROOT/.venv"
fi
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"

if ! python -c "import fastapi, uvicorn, openai, temporalio" >/dev/null 2>&1; then
    echo "    installing API dependencies (first run — this takes a minute)..."
    pip install --upgrade pip --quiet
    pip install -r "$ROOT/api/requirements.txt" --quiet
    pip install pytest pytest-asyncio anyio ruff --quiet
else
    echo "    API dependencies already installed ✓"
fi

# ---------------------------------------------------------------------------
# 3. Environment files
# ---------------------------------------------------------------------------
step "Checking environment files"

if [ ! -f "$ROOT/api/.env" ]; then
    cp "$ROOT/api/.env.example" "$ROOT/api/.env"
    echo "    created api/.env from template"
    warn "API keys in api/.env are placeholders — edit OPENCODE_API_KEY / LLAMA_CLOUD_API_KEY for real extractions."
else
    echo "    api/.env exists ✓"
fi

if [ ! -f "$ROOT/web/.env" ]; then
    cp "$ROOT/web/.env.example" "$ROOT/web/.env"
    echo "    created web/.env from template"
else
    echo "    web/.env exists ✓"
fi

# ---------------------------------------------------------------------------
# 4. Web dependencies
# ---------------------------------------------------------------------------
step "Setting up Web dependencies"

if [ ! -d "$ROOT/web/node_modules" ]; then
    echo "    running npm install (first run — this takes a minute)..."
    (cd "$ROOT/web" && npm install --no-fund --no-audit)
else
    echo "    node_modules already installed ✓"
fi

# ---------------------------------------------------------------------------
# 5. Run
# ---------------------------------------------------------------------------
cleanup() {
    echo
    echo "==> Stopping services..."
    kill 0 2>/dev/null || true
}
trap cleanup EXIT INT TERM

step "Starting API  → http://127.0.0.1:8000  (docs: /docs)"
(cd "$ROOT/api" && python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000) &

step "Starting Web  → http://localhost:5173"
(cd "$ROOT/web" && npm run dev) &

echo
echo "============================================================"
echo "  DocExtract is running"
echo "    UI  : http://localhost:5173"
echo "    API : http://127.0.0.1:8000/docs"
echo "  Press Ctrl+C to stop both"
echo "============================================================"
wait
