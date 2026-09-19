#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-${CONDUCTOR_PORT:-7860}}"
AGENT_RUNTIME="${AGENT_RUNTIME:-virtual-agents}"

if [[ "$AGENT_RUNTIME" != "virtual-agents" && "$AGENT_RUNTIME" != "python" ]]; then
  echo "AGENT_RUNTIME must be virtual-agents or python." >&2
  exit 2
fi

cd "$ROOT_DIR"
npm --prefix src/frontend run build

echo "Dashboard: http://localhost:$PORT/app/"
echo "Voice socket: ws://localhost:$PORT/ws"
echo "Agent runtime: $AGENT_RUNTIME"

if [[ "$AGENT_RUNTIME" == "python" ]]; then
  exec env PORT="$PORT" PYTHONPATH="$ROOT_DIR/src" uv run python -m agent
fi

exec env PORT="$PORT" npm --prefix virtual-agents run dev
