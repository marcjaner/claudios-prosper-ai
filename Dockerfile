# Build the React console once, then let FastAPI serve it together with the
# WebSocket endpoint. Keeping both behind one origin is important for /ws.
FROM node:22-alpine AS frontend

WORKDIR /app/src/frontend
COPY src/frontend/package.json ./
RUN npm install
COPY src/frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite+aiosqlite:////data/agent.db \
    CALLS_DB=/data/calls.db

COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project
COPY src/ ./src/
COPY graphs/ ./graphs/
COPY --from=frontend /app/src/frontend/dist ./src/frontend/dist

EXPOSE 7860
CMD ["sh", "-c", "uv run --no-sync uvicorn agent.server:app --host 0.0.0.0 --port ${PORT:-7860}"]
