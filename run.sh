#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$ROOT_DIR/src/frontend"
FRONTEND_PID=""
BACKEND_PID=""

free_port() {
  uv run python -c 'import socket; sock = socket.socket(); sock.bind(("127.0.0.1", 0)); print(sock.getsockname()[1]); sock.close()'
}

port_is_free() {
  uv run python -c 'import socket, sys; sock = socket.socket(); result = sock.connect_ex(("127.0.0.1", int(sys.argv[1]))); sock.close(); raise SystemExit(result == 0)' "$1"
}

choose_port() {
  local preferred="${1:-}"
  if [[ -n "$preferred" ]] && port_is_free "$preferred"; then
    echo "$preferred"
    return
  fi
  free_port
}

cleanup() {
  trap - TERM INT EXIT
  [[ -z "$BACKEND_PID" ]] || kill "$BACKEND_PID" 2>/dev/null || true
  [[ -z "$FRONTEND_PID" ]] || kill "$FRONTEND_PID" 2>/dev/null || true
  [[ -z "$BACKEND_PID" ]] || wait "$BACKEND_PID" 2>/dev/null || true
  [[ -z "$FRONTEND_PID" ]] || wait "$FRONTEND_PID" 2>/dev/null || true
}

trap cleanup TERM INT EXIT

FRONTEND_PREFERRED="${APP_FRONTEND_PORT:-${CONDUCTOR_PORT:-}}"
if [[ -n "${APP_BACKEND_PORT:-}" ]]; then
  BACKEND_PREFERRED="$APP_BACKEND_PORT"
elif [[ -n "${CONDUCTOR_PORT:-}" ]]; then
  BACKEND_PREFERRED="$((CONDUCTOR_PORT + 1))"
else
  BACKEND_PREFERRED=""
fi

APP_FRONTEND_PORT="$(choose_port "$FRONTEND_PREFERRED")"
APP_BACKEND_PORT="$(choose_port "$BACKEND_PREFERRED")"
while [[ "$APP_BACKEND_PORT" == "$APP_FRONTEND_PORT" ]]; do
  APP_BACKEND_PORT="$(free_port)"
done
export APP_BACKEND_PORT

cd "$FRONTEND_DIR"
npm install
echo "Building dashboard bundle from $FRONTEND_DIR"
npm run build
test -f "$FRONTEND_DIR/dist/index.html"
echo "Dashboard bundle ready: $FRONTEND_DIR/dist/index.html"
cd "$ROOT_DIR"
uv run uvicorn agent.server:app --host 0.0.0.0 --port "$APP_BACKEND_PORT" --env-file .env &
BACKEND_PID=$!

for attempt in {1..60}; do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "Backend exited before becoming ready." >&2
    exit 1
  fi
  if curl --silent --fail "http://127.0.0.1:$APP_BACKEND_PORT/health" >/dev/null; then
    break
  fi
  sleep 1
done

if ! curl --silent --fail "http://127.0.0.1:$APP_BACKEND_PORT/health" >/dev/null; then
  echo "Backend did not become ready within 60 seconds." >&2
  exit 1
fi

cd "$FRONTEND_DIR"
# A previous run can leave Vite alive after the shell is interrupted. Kill only
# Vite processes launched from this project so there is only one dashboard.
pkill -f "$FRONTEND_DIR/node_modules/.bin/vite" 2>/dev/null || true
npm run dev -- --host 0.0.0.0 --port "$APP_FRONTEND_PORT" --force --strictPort &
FRONTEND_PID=$!

echo
echo "Frontend: http://localhost:$APP_FRONTEND_PORT/"
echo "Backend:  http://localhost:$APP_BACKEND_PORT/"
echo "Press Ctrl+C to stop both servers."
echo

wait -n "$BACKEND_PID" "$FRONTEND_PID"
