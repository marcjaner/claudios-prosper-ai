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
npm run build
cd "$ROOT_DIR"
uv run uvicorn agent.server:app --host 0.0.0.0 --port 7860 --env-file .env &
BACKEND_PID=$!

cd "$FRONTEND_DIR"
npm run dev -- --host 0.0.0.0 &
FRONTEND_PID=$!

echo
echo "Frontend: http://localhost:5173/app/"
echo "Backend:  http://localhost:7860/"
echo "Press Ctrl+C to stop both servers."
echo

wait -n "$BACKEND_PID" "$FRONTEND_PID"
