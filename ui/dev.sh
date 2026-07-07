#!/usr/bin/env bash
# Dev mode: API on :8787 + Vite HMR on :5173 (proxies /api and /media)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/ui/app"
PORT="${IMAGE_PIPELINE_UI_PORT:-8787}"

pick_python() {
  # claude-agent-sdk (AI prompt drafting) needs Python 3.10+; the system
  # /usr/bin/python3 stub on macOS is often 3.9. Prefer a modern interpreter.
  for candidate in python3.13 python3.12 python3.11 python3.10; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return
    fi
  done
  command -v python3
}

venv_python_is_modern_enough() {
  [[ -x "$ROOT/ui/.venv/bin/python3" ]] || return 1
  "$ROOT/ui/.venv/bin/python3" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
}

free_port() {
  local port=$1
  local label="${2:-process}"
  local pids
  pids=$(lsof -ti ":${port}" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "Stopping existing ${label} on :${port}…"
    kill $pids 2>/dev/null || true
    sleep 0.5
  fi
}

PY=$(pick_python)

if [[ -d "$ROOT/ui/.venv" ]] && ! venv_python_is_modern_enough; then
  echo "ui/.venv is on Python < 3.10 (claude-agent-sdk needs 3.10+) — rebuilding with $PY…"
  rm -rf "$ROOT/ui/.venv"
fi

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

free_port "$PORT" "API server"
VITE_PORT="${IMAGE_PIPELINE_VITE_PORT:-5173}"
free_port "$VITE_PORT" "Vite dev server"

echo "API server  → http://127.0.0.1:${PORT}"
echo "Vite dev UI → http://127.0.0.1:${VITE_PORT}  (proxies API to :${PORT})"
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
