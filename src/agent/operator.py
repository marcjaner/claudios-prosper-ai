"""HTTP surface for clinic staff to take a live call away from the agent."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pipecat.frames.frames import EndFrame, TTSSpeakFrame
from pipecat.transcriptions.language import Language

from observability import emit, update_call

from .clinic_api import ClinicApi, ProsperApiError
from .clinic_models import OutcomeReason, OutcomeRequest
from .language import DEFAULT_LANGUAGE, phrases

ESCALATION_TOOL = "escalate_to_human"
ESCALATION_ROUTE = "/api/v1/submit/escalate"
ESCALATION_OUTCOME = "ESCALATE"
# Staff escalate for their own reasons; the platform only knows this catalogue.
ESCALATION_REASON = OutcomeReason.OUT_OF_SCOPE


def register_operator_api(app: FastAPI) -> None:
    @app.post("/api/calls/{call_id}/escalate")
    async def escalate_call(request: Request, call_id: str) -> dict[str, Any]:
        worker = request.app.state.active_workers.get(call_id)
        if worker is None:
            raise HTTPException(status_code=409, detail="call is no longer active")

        submission = OutcomeRequest(call_id=call_id, reason=ESCALATION_REASON)
        try:
            output = await asyncio.to_thread(
                ClinicApi.from_environment().escalate, submission
            )
        except ProsperApiError as error:
            raise HTTPException(status_code=502, detail=str(error.detail)) from error

        arguments = {"reason": ESCALATION_REASON.value}
        emit(call_id, "operator_escalate", arguments)
        _record_escalation(call_id, arguments, output)
        await request.app.state.guardrail_repository.record_submission(
            call_id, ESCALATION_OUTCOME, arguments, 200, {"output": output}
        )

        farewell = phrases(_call_language(request.app.state.store, call_id)).escalated
        emit(call_id, "tts", {"text": farewell})
        await worker.queue_frames(
            [TTSSpeakFrame(farewell, append_to_context=False), EndFrame()]
        )
        return {"status": "escalated", "call_id": call_id}


def _record_escalation(call_id: str, arguments: dict[str, Any], output: Any) -> None:
    # The same trail the agent leaves when it escalates, so the transcript,
    # the action graph and the history need no notion of a manual escalation.
    tool_call_id = uuid4().hex
    emit(call_id, "tool_call", {
        "name": ESCALATION_TOOL, "tool_call_id": tool_call_id, "arguments": arguments,
    })
    emit(call_id, "tool_result", {
        "name": ESCALATION_TOOL, "tool_call_id": tool_call_id, "status": 200,
        "ms": 0, "response": output,
    })
    emit(call_id, "submit", {
        "route": ESCALATION_ROUTE, "status": 200, "request": arguments, "response": output,
    })
    update_call(
        call_id,
        outcome=ESCALATION_OUTCOME,
        reason=ESCALATION_REASON.value,
        state="stopping",
    )


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
