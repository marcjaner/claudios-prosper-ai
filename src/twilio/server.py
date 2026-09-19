import asyncio
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from loguru import logger
from observability import EventBus, Store, set_bus
from observability.api import register_dashboard
from sqlalchemy.engine import make_url

from .transport import AgentFactory, run_call

WS_PATH = "/ws"
CALL_TESTER_PATH = "/"
CALL_TESTER_FILE = Path(__file__).resolve().parents[1] / "frontend" / "call_tester.html"
DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///data/agent.db"
DATABASE_SAMPLE_ROWS = 25
# Overridable so a seeded fixture never lands in the weekend's real history.
CONSOLE_DB_PATH = Path(os.getenv("CALLS_DB", "calls.db"))
VITE_DEV_SERVER = "http://localhost:5173"


def read_database_snapshot() -> dict[str, Any]:
    url = make_url(os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))
    if url.get_backend_name() != "sqlite":
        return {"error": "The local database viewer supports SQLite only."}

    database_path = Path(url.database or "")
    if not database_path.exists():
        return {
            "path": str(database_path),
            "tables": [],
            "message": "Database file does not exist yet.",
        }

    with sqlite3.connect(database_path) as connection:
        table_names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        tables = []
        for name in table_names:
            quoted_name = name.replace('"', '""')
            cursor = connection.execute(
                f'SELECT * FROM "{quoted_name}" LIMIT ?', (DATABASE_SAMPLE_ROWS,)
            )
            columns = [column[0] for column in cursor.description]
            rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
            count = connection.execute(
                f'SELECT COUNT(*) FROM "{quoted_name}"'
            ).fetchone()[0]
            tables.append(
                {"name": name, "count": count, "columns": columns, "rows": rows}
            )

    return {"path": str(database_path), "tables": tables}


def create_app(build_agent: AgentFactory) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(CONSOLE_DB_PATH)
        bus = EventBus(store)
        app.state.store = store
        set_bus(bus)
        drain = asyncio.create_task(bus.run())
        try:
            yield
        finally:
            drain.cancel()
            set_bus(None)
            store.close()

    app = FastAPI(lifespan=lifespan)

    # For the Vite dev server only. This has nothing to do with
    # FastAPIWebsocketParams(allowed_origins=[]), which guards the Twilio
    # socket, and browsers do not enforce CORS on WebSockets at all.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[VITE_DEV_SERVER],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get(CALL_TESTER_PATH, include_in_schema=False)
    async def call_tester() -> FileResponse:
        # This page is deliberately served by the call server. Its WebSocket
        # therefore follows the exact same path as Twilio, with no proxy or
        # browser-only backend to keep in sync.
        return FileResponse(CALL_TESTER_FILE)

    @app.get("/api/debug/database", include_in_schema=False)
    async def database_snapshot() -> dict[str, Any]:
        return await asyncio.to_thread(read_database_snapshot)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "ws_path": WS_PATH}

    @app.websocket(WS_PATH)
    async def call(websocket: WebSocket) -> None:
        # One pipeline per socket. A Run All opens ten at once and problem 2
        # opens twenty; nothing may be shared between them.
        try:
            await run_call(websocket, build_agent)
        except Exception:  # noqa: BLE001 - never let one call escape into the server
            logger.exception("unhandled error serving call")

    # The call tester owns /, so the console is mounted under /app.
    register_dashboard(app)
    return app
