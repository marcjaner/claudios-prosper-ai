#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$ROOT_DIR/src/frontend"
FRONTEND_PID=""
BACKEND_PID=""

cleanup() {
  trap - TERM INT EXIT
  [[ -z "$BACKEND_PID" ]] || kill "$BACKEND_PID" 2>/dev/null || true
  [[ -z "$FRONTEND_PID" ]] || kill "$FRONTEND_PID" 2>/dev/null || true
  [[ -z "$BACKEND_PID" ]] || wait "$BACKEND_PID" 2>/dev/null || true
  [[ -z "$FRONTEND_PID" ]] || wait "$FRONTEND_PID" 2>/dev/null || true
}

trap cleanup TERM INT EXIT

cd "$FRONTEND_DIR"
npm install
echo "Building dashboard bundle from $FRONTEND_DIR"
npm run build
test -f "$FRONTEND_DIR/dist/index.html"
echo "Dashboard bundle ready: $FRONTEND_DIR/dist/index.html"
cd "$ROOT_DIR"
uv run uvicorn agent.server:app --host 0.0.0.0 --port 7860 --env-file .env &
BACKEND_PID=$!

for attempt in {1..60}; do
  if curl --silent --fail http://127.0.0.1:7860/health >/dev/null; then
    break
  fi
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "Backend exited before becoming ready." >&2
    exit 1
  fi
  sleep 1
done

if ! curl --silent --fail http://127.0.0.1:7860/health >/dev/null; then
  echo "Backend did not become ready within 60 seconds." >&2
  exit 1
fi

cd "$FRONTEND_DIR"
# A previous run can leave Vite alive after the shell is interrupted. Kill only
# Vite processes launched from this project so the dashboard always owns 5173.
pkill -f "$FRONTEND_DIR/node_modules/.bin/vite" 2>/dev/null || true
npm run dev -- --host 0.0.0.0 --force --strictPort &
FRONTEND_PID=$!

echo
echo "Frontend: http://localhost:5173/app/"
echo "Backend:  http://localhost:7860/"
echo "Press Ctrl+C to stop both servers."
echo

wait -n "$BACKEND_PID" "$FRONTEND_PID"
