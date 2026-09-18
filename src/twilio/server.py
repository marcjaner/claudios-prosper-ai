from fastapi import FastAPI, WebSocket
from loguru import logger

from .transport import AgentFactory, run_call

WS_PATH = "/ws"


def create_app(build_agent: AgentFactory) -> FastAPI:
    app = FastAPI()

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

    return app
