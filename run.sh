#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-${CONDUCTOR_PORT:-7860}}"

cd "$ROOT_DIR"
npm --prefix src/frontend run build

echo "Dashboard: http://localhost:$PORT/app/"
echo "Voice socket: ws://localhost:$PORT/ws"
PORT="$PORT" npm --prefix virtual-agents run dev
