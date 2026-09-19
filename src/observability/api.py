from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from loguru import logger

from . import subscribe
from .bus import CallUpdate

DASHBOARD_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


def register_dashboard(app: FastAPI) -> None:
    @app.get("/api/calls")
    async def list_calls(
        request: Request,
        limit: int = 200,
        offset: int = 0,
        outcome: str | None = None,
        reason: str | None = None,
        q: str | None = None,
        ended_only: bool = False,
        name: str | None = None,
        insurer: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict:
        return {
            "calls": request.app.state.store.list_calls(
                limit=limit,
                offset=offset,
                outcome=outcome,
                reason=reason,
                search=q,
                ended_only=ended_only,
                name=name,
                insurer=insurer,
                date_from=date_from,
                date_to=date_to,
            )
        }

    @app.get("/api/histogram")
    async def histogram(
        request: Request,
        outcome: str | None = None,
        reason: str | None = None,
        q: str | None = None,
        ended_only: bool = False,
        name: str | None = None,
        insurer: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict:
        return request.app.state.store.histogram(
            outcome=outcome,
            reason=reason,
            search=q,
            ended_only=ended_only,
            name=name,
            insurer=insurer,
            date_from=date_from,
            date_to=date_to,
        )

    @app.get("/api/stats")
    async def stats(request: Request) -> dict:
        return request.app.state.store.stats()

    @app.get("/api/calls/{call_id}")
    async def get_call(request: Request, call_id: str) -> dict:
        store = request.app.state.store
        call = store.get_call(call_id)
        if call is None:
            raise HTTPException(status_code=404, detail="no such call")
        return {"call": call, "events": store.get_events(call_id)}

    @app.websocket("/api/live")
    async def live(websocket: WebSocket) -> None:
        await websocket.accept()
        # The snapshot is what lets a browser refresh mid-call without losing
        # anything; everything after it is a patch.
        await websocket.send_json(
            {"type": "snapshot", "calls": websocket.app.state.store.list_calls()}
        )
        try:
            with subscribe() as queue:
                while True:
                    item = await queue.get()
                    await websocket.send_json(_message(item))
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001 - one dashboard tab is not the server
            logger.exception("live dashboard socket failed")

    # Mounted at /app, not /: a root mount matches everything and shadows
    # /health and /ws the moment route ordering shifts.
    if DASHBOARD_DIST.exists():
        app.mount("/app", StaticFiles(directory=DASHBOARD_DIST, html=True), name="app")


def _message(item) -> dict:
    # Fields travel as sent rather than re-read from the store: the broadcast
    # runs before the commit, so a lookup here would race the writer.
    if isinstance(item, CallUpdate):
        return {"type": "call", "call_id": item.call_id, "fields": item.fields}
    return {
        "type": "event",
        "call_id": item.call_id,
        "ts": item.ts,
        "kind": item.kind,
        "payload": item.payload,
    }
