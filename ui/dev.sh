#!/usr/bin/env bash
# Dev mode: API on :8787 + Vite HMR on :5173 (proxies /api and /media)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/ui/app"
PORT="${IMAGE_PIPELINE_UI_PORT:-8787}"

pick_python() {
  command -v python3
}

free_port() {
  local port=$1
  local pids
  pids=$(lsof -ti ":${port}" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "Stopping existing process on :${port} (stale API server)…"
    kill $pids 2>/dev/null || true
    sleep 0.5
  fi
}

PY=$(pick_python)

if [[ ! -d "$ROOT/ui/.venv" ]]; then
  "$PY" -m venv "$ROOT/ui/.venv"
fi
# shellcheck source=/dev/null
source "$ROOT/ui/.venv/bin/activate"
python -m pip install -q -r "$ROOT/ui/requirements.txt"

if [[ ! -d "$APP/node_modules" ]]; then
  echo "Installing UI frontend dependencies…"
  (cd "$APP" && npm install)
fi

free_port "$PORT"

echo "API server  → http://127.0.0.1:${PORT}"
echo "Vite dev UI → http://127.0.0.1:5173  (proxies API to :${PORT})"
echo "Press Ctrl+C to stop both."

export IMAGE_PIPELINE_UI_PORT="$PORT"
python "$ROOT/ui/server.py" &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true' EXIT INT TERM

sleep 1
if ! curl -sf "http://127.0.0.1:${PORT}/api/health" >/dev/null; then
  echo "ERROR: API failed to start on :${PORT}. Check ui/server.py output above." >&2
  exit 1
fi
if ! curl -sf "http://127.0.0.1:${PORT}/api/products" >/dev/null; then
  echo "ERROR: /api/products missing — wrong server.py still running?" >&2
  exit 1
fi
echo "API OK (health + products)"

cd "$APP"
exec npm run dev
