#!/usr/bin/env bash
# Run the whole stack with ONE command: API (:8000) + Web (:5173).
# Ctrl+C stops both. First run creates .venv and installs dependencies.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cleanup() {
    echo
    echo "==> Stopping services..."
    kill 0 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# --- API (FastAPI) ---
if [ ! -d "$ROOT/.venv" ]; then
    echo "==> Creating .venv and installing API dependencies (first run)"
    python3 -m venv "$ROOT/.venv"
    # shellcheck disable=SC1091
    source "$ROOT/.venv/bin/activate"
    pip install --upgrade pip >/dev/null
    pip install -r "$ROOT/api/requirements.txt" pytest pytest-asyncio anyio ruff >/dev/null
else
    # shellcheck disable=SC1091
    source "$ROOT/.venv/bin/activate"
fi

[ -f "$ROOT/api/.env" ] || cp "$ROOT/api/.env.example" "$ROOT/api/.env"

echo "==> Starting API on http://127.0.0.1:8000 (docs: /docs)"
(cd "$ROOT/api" && python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000) &

# --- Web (React) ---
echo "==> Starting Web on http://localhost:5173"
(cd "$ROOT/web" && [ -d node_modules ] || npm install; npm run dev) &

echo
echo "==> Ready: UI http://localhost:5173 · API http://127.0.0.1:8000/docs"
wait
