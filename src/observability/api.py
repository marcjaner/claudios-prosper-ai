import asyncio
import json
import os
import time
from pathlib import Path

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.staticfiles import StaticFiles
from loguru import logger

from . import emit, subscribe, update_call
from .bus import CallUpdate

DASHBOARD_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
DEMO_LIVE_CALLS = (
    Path(__file__).resolve().parents[2] / "scripts" / "demo_live_calls.json"
)


async def _mark_demo_call_for_attention(call_id: str, delay: float) -> None:
    await asyncio.sleep(delay)
    update_call(
        call_id,
        guardrail_breached=1,
        guardrail_reason="Clinic staff review requested",
    )


def register_dashboard(app: FastAPI) -> None:
    @app.get("/api/guardrails")
    async def list_guardrails(request: Request) -> dict:
        rows = await request.app.state.guardrail_repository.list_guardrails()
        return {"guardrails": [{"id": row.id, "title": row.title, "description": row.description or row.text} for row in rows]}

    @app.put("/api/guardrails")
    async def replace_guardrails(request: Request) -> dict:
        payload = await request.json()
        rules = payload.get("guardrails")
        if not isinstance(rules, list) or not all(isinstance(rule, dict) for rule in rules):
            raise HTTPException(status_code=422, detail="guardrails must be a list of objects")
        try:
            rows = await request.app.state.guardrail_repository.replace_guardrails(rules)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"guardrails": [{"id": row.id, "title": row.title, "description": row.description} for row in rows]}

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
        started_after: float | None = Query(default=None, ge=0, allow_inf_nan=False),
        started_before: float | None = Query(default=None, ge=0, allow_inf_nan=False),
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
                started_after=started_after,
                started_before=started_before,
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
        started_after: float | None = Query(default=None, ge=0, allow_inf_nan=False),
        started_before: float | None = Query(default=None, ge=0, allow_inf_nan=False),
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
            started_after=started_after,
            started_before=started_before,
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

    @app.post("/api/calls/{call_id}/stop")
    async def stop_call(request: Request, call_id: str) -> dict:
        worker = request.app.state.active_workers.get(call_id)
        if worker is None:
            if os.getenv("DEMO_MODE") == "1" and call_id.startswith("CAlive"):
                emit(call_id, "operator_stop", {})
                update_call(call_id, state="ended", ended_at=time.time())
                return {"status": "stopped", "call_id": call_id}
            raise HTTPException(status_code=409, detail="call is no longer active")
        emit(call_id, "operator_stop", {})
        update_call(call_id, state="stopping")
        await worker.cancel(reason="stopped by operator")
        return {"status": "stopping", "call_id": call_id}

    @app.post("/api/demo/start")
    async def start_demo_calls() -> dict:
        if os.getenv("DEMO_MODE") != "1":
            raise HTTPException(status_code=404, detail="demo mode is disabled")
        now = time.time()
        calls = json.loads(DEMO_LIVE_CALLS.read_text())["calls"]
        for fixture in calls:
            started_at = now - fixture["started_seconds_ago"]
            fields = {
                key: value
                for key, value in fixture.items()
                if key
                not in {
                    "call_id",
                    "started_seconds_ago",
                    "attention_after_seconds",
                    "events",
                }
            }
            fields.update(
                started_at=started_at,
                ended_at=None,
                score_overall=None,
                score_json=None,
                score_error=None,
                guardrail_breached=0,
                guardrail_reason=None,
                guardrail_violations=None,
            )
            update_call(fixture["call_id"], **fields)
            if delay := fixture.get("attention_after_seconds"):
                asyncio.create_task(
                    _mark_demo_call_for_attention(fixture["call_id"], delay)
                )
            for event in fixture["events"]:
                emit(
                    fixture["call_id"],
                    event["kind"],
                    event["payload"],
                    ts=started_at + event["at"],
                )
        return {"started": len(calls)}

    @app.websocket("/api/live")
    async def live(websocket: WebSocket) -> None:
        await websocket.accept()
        # The snapshot is what lets a browser refresh mid-call without losing
        # anything; everything after it is a patch.
        calls = websocket.app.state.store.list_calls()
        await websocket.send_json(
            {
                "type": "snapshot",
                "calls": calls,
                "events": {
                    call["call_id"]: websocket.app.state.store.get_events(
                        call["call_id"]
                    )
                    for call in calls
                    if call["ended_at"] is None
                },
            }
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


def mount_dashboard(app: FastAPI) -> None:
    # A root mount matches every path, so it must be the last thing registered:
    # any API, health, or WebSocket route added after it is never reached.
    if DASHBOARD_DIST.exists():
        app.mount("/", StaticFiles(directory=DASHBOARD_DIST, html=True), name="dashboard")


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
