#!/usr/bin/env bash
# Bootstrap UI venv, build React frontend if Node available, start server
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/ui/.venv"
APP="$ROOT/ui/app"

pick_python() {
  for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
      ver=$("$cmd" -c 'import sys; print(sys.version_info[:2])')
      major=${ver%%,*}
      minor=${ver##*, }
      minor=${minor// /}
      if [[ "$major" -eq 3 && "$minor" -ge 10 ]]; then
        echo "$cmd"
        return 0
      fi
    fi
  done
  command -v python3
}

PY=$(pick_python)
echo "Using $PY ($($PY --version))"

if command -v npm >/dev/null 2>&1 && [[ -f "$APP/package.json" ]]; then
  if [[ ! -d "$APP/node_modules" ]]; then
    echo "Installing UI frontend dependencies…"
    (cd "$APP" && npm install)
  fi
  echo "Building React UI → ui/static/"
  (cd "$APP" && npm run build)
else
  echo "Note: npm not found — using pre-built ui/static (run: cd ui/app && npm install && npm run build)"
fi

if [[ ! -d "$VENV" ]]; then
  "$PY" -m venv "$VENV"
fi
# shellcheck source=/dev/null
source "$VENV/bin/activate"
python -m pip install -q -U pip
python -m pip install -q -r "$ROOT/ui/requirements.txt"
exec python "$ROOT/ui/server.py"
