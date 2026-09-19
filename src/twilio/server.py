import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from observability import EventBus, Store, set_bus
from observability.api import register_dashboard

from .transport import AgentFactory, run_call

WS_PATH = "/ws"
# Overridable so a seeded fixture never lands in the weekend's real history.
DB_PATH = Path(os.getenv("CALLS_DB", "calls.db"))
VITE_DEV_SERVER = "http://localhost:5173"


def create_app(build_agent: AgentFactory) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(DB_PATH)
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

    register_dashboard(app)
    return app
