"""The socket a member of staff opens from the dashboard to take a live call over."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.transcriptions.language import Language

from observability import emit, update_call

from .clinic_api import ClinicApi, ProsperApiError
from .clinic_models import OutcomeReason, OutcomeRequest
from .language import DEFAULT_LANGUAGE, phrases

logger = logging.getLogger(__name__)

ESCALATION_TOOL = "escalate_to_human"
ESCALATION_ROUTE = "/api/v1/submit/escalate"
ESCALATION_OUTCOME = "ESCALATE"
# Staff take calls for their own reasons; the platform only knows this catalogue.
ESCALATION_REASON = OutcomeReason.OUT_OF_SCOPE
CALL_NOT_ACTIVE = 4409
# Sent as text once the agent has finished its hand-off phrase; the browser
# holds its microphone until then so the caller never hears both at once.
LINE_OPEN = "live"


def register_operator_api(app: FastAPI) -> None:
    @app.websocket("/api/calls/{call_id}/operator")
    async def operator_line(websocket: WebSocket, call_id: str) -> None:
        active = app.state.active_calls.get(call_id)
        if active is None or active.bridge.active:
            await websocket.close(code=CALL_NOT_ACTIVE)
            return

        await websocket.accept()
        handoff = phrases(_call_language(app.state.store, call_id)).handoff
        emit(call_id, "tts", {"text": handoff})
        await active.worker.queue_frame(TTSSpeakFrame(handoff, append_to_context=False))
        update_call(call_id, state="operator", handled_by="operator")
        submission = None
        if (app.state.store.get_call(call_id) or {}).get("outcome") is None:
            submission = asyncio.create_task(_submit_escalation(app, call_id))

        try:
            await active.bridge.take_over(after_handoff=True)
            emit(call_id, "operator_takeover", {})
            await websocket.send_text(LINE_OPEN)
            await _bridge_audio(websocket, active.bridge)
        finally:
            emit(call_id, "operator_hangup", {})
            if submission is not None:
                await submission
            if app.state.active_calls.get(call_id) is active:
                await active.worker.cancel(reason="operator hung up")


async def _bridge_audio(websocket: WebSocket, bridge) -> None:
    async def caller_to_operator():
        while (pcm := await bridge.caller_audio.get()) is not None:
            await websocket.send_bytes(pcm)

    async def operator_to_caller():
        while True:
            await bridge.speak(await websocket.receive_bytes())

    tasks = [
        asyncio.create_task(caller_to_operator()),
        asyncio.create_task(operator_to_caller()),
    ]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            error = task.exception()
            if error is not None and not isinstance(error, WebSocketDisconnect):
                logger.warning("operator line failed", exc_info=error)
    finally:
        for task in tasks:
            task.cancel()


async def _submit_escalation(app: FastAPI, call_id: str) -> None:
    # The record is what scores, and a human taking the call is an escalation.
    request = OutcomeRequest(call_id=call_id, reason=ESCALATION_REASON)
    try:
        api = ClinicApi.from_environment()
    except RuntimeError as error:
        logger.warning("escalation not submitted | call_id=%s %s", call_id, error)
        return
    try:
        output = await asyncio.to_thread(api.escalate, request)
    except ProsperApiError as error:
        logger.warning("escalation rejected | call_id=%s %s", call_id, error)
        emit(call_id, "error", {"message": f"Escalation not recorded: {error.detail}"})
        return
    finally:
        await asyncio.to_thread(api.close)
    arguments = {"reason": ESCALATION_REASON.value}
    _record_escalation(call_id, arguments, output)
    await app.state.guardrail_repository.record_submission(
        call_id, ESCALATION_OUTCOME, arguments, 200, {"output": output}
    )


def _record_escalation(call_id: str, arguments: dict[str, Any], output: Any) -> None:
    # The same trail the agent leaves when it escalates, so the transcript,
    # the action graph and the history need no notion of a manual escalation.
    tool_call_id = uuid4().hex
    emit(
        call_id,
        "tool_call",
        {
            "name": ESCALATION_TOOL,
            "tool_call_id": tool_call_id,
            "arguments": arguments,
        },
    )
    emit(
        call_id,
        "tool_result",
        {
            "name": ESCALATION_TOOL,
            "tool_call_id": tool_call_id,
            "status": 200,
            "ms": 0,
            "response": output,
        },
    )
    emit(
        call_id,
        "submit",
        {
            "route": ESCALATION_ROUTE,
            "status": 200,
            "request": arguments,
            "response": output,
        },
    )
    update_call(call_id, outcome=ESCALATION_OUTCOME, reason=ESCALATION_REASON.value)


def _call_language(store: Any, call_id: str) -> Language:
    # The agent only announces departures from the default language, so a call
    # without a language_changed event was spoken in the default throughout.
    events = store.get_events(call_id)
    changes = [event for event in events if event["kind"] == "language_changed"]
    if not changes:
        return DEFAULT_LANGUAGE
    try:
        return Language(changes[-1]["payload"]["language"])
    except (KeyError, ValueError):
        return DEFAULT_LANGUAGE
