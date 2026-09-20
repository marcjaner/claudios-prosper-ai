import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
import simulate_call
from fastapi import FastAPI, WebSocket
from simulation import (
    LocalClinicApi,
    caller_profile,
    caller_prompt,
    load_case,
    outcome_matches,
)
from test_twilio_transport import BackgroundServer

from agent.clinic_api import ProsperApiError
from agent.clinic_models import BookRequest


def booking(call_id="LOCAL1"):
    return BookRequest(
        call_id=call_id,
        patient_id="P1",
        provider_id="PR1",
        location_id="centro",
        appointment_type_id="review",
        slot="2026-09-21T10:00:00+02:00",
        policy_id="privado",
    )


def test_submissions_are_captured_with_no_network_request(monkeypatch):
    api = LocalClinicApi("https://example.invalid", "fake")
    monkeypatch.setattr(
        api._client, "post", lambda *_args, **_kwargs: pytest.fail("real POST")
    )
    api.start_call("LOCAL1", 180)
    try:
        response = api.book(booking())
        assert response["record"]["actions"] == api.actions
        assert api.actions[0]["action"] == "BOOK"
        with pytest.raises(ProsperApiError) as duplicate:
            api.book(booking())
        assert duplicate.value.status_code == 409
        assert len(api.actions) == 1
    finally:
        api.shutdown()


def test_http_guard_blocks_accidental_platform_writes():
    with pytest.raises(RuntimeError, match="forbids"):
        LocalClinicApi._read_only(
            httpx.Request("POST", "https://example.invalid/api/v1/submit/book")
        )
    LocalClinicApi._read_only(
        httpx.Request("GET", "https://example.invalid/api/v1/directory")
    )


def test_late_and_wrong_call_submissions_are_not_accepted():
    api = LocalClinicApi("https://example.invalid", "fake")
    api.start_call("LOCAL1", 180)
    try:
        with pytest.raises(ProsperApiError) as unknown:
            api.book(booking("REAL-CALL"))
        assert unknown.value.status_code == 404
        api.deadline = time.monotonic() - 1
        with pytest.raises(ProsperApiError) as late:
            api.book(booking())
        assert late.value.status_code == 410
        assert api.actions == []
    finally:
        api.shutdown()


def test_caller_does_not_receive_the_expected_answer():
    case = {
        "caller": {"persona": "I want a GP appointment", "facts": {"name": "Ana"}},
        "accepted": [{"actions": [{"action": "BOOK", "slot": "SECRET_EXPECTED_SLOT"}]}],
    }
    profile = caller_profile(case, None)
    prompt = caller_prompt(profile, [])
    assert "Ana" in prompt
    assert "SECRET_EXPECTED_SLOT" not in prompt
    assert "accepted" not in profile


def test_incomplete_public_personas_require_explicit_scenarios():
    with pytest.raises(ValueError, match="explicit YAML"):
        load_case(None, "triage-123aaa365997")


def test_extra_refusal_fails_even_if_the_booking_is_correct():
    booked = {"action": "BOOK", "slot": "2026-09-21T10:00:00+02:00", "patient_id": "P1"}
    expected = [{"actions": [booked]}]
    assert outcome_matches([booked], expected)
    assert not outcome_matches(
        [{"action": "NO_ACTION", "reason": "not_eligible_age"}, booked], expected
    )
    assert not outcome_matches([], expected)
    assert not outcome_matches([{**booked, "slot": "tomorrow morning"}], expected)
    assert outcome_matches([{**booked, "slot": "2026-09-21T08:00:00+00:00"}], expected)


def test_deadline_closes_an_actual_socket_and_stops_audio(monkeypatch, tmp_path):
    async def never_finishes(*_):
        await asyncio.sleep(10)

    monkeypatch.setattr(simulate_call, "conversation", never_finishes)
    events = []
    app = FastAPI()

    @app.websocket("/ws")
    async def phone(websocket: WebSocket):
        await websocket.accept()
        async for raw in websocket.iter_text():
            events.append(json.loads(raw)["event"])

    async def run():
        api = LocalClinicApi("https://example.invalid", "fake")
        report = {
            "call_id": "LOCAL1",
            "limit_seconds": 0.12,
            "directory": str(tmp_path),
        }
        try:
            async with BackgroundServer(app) as server:
                await simulate_call.dial(server.ws_url, {}, api, report)
            assert report["ending"] == "deadline"
            assert report["duration_seconds"] < 0.5
            assert events[:2] == ["connected", "start"]
            assert "media" in events
            assert events[-1] == "stop"
        finally:
            api.shutdown()

    asyncio.run(run())


def test_case_files_have_separate_caller_and_scoring_data():
    import yaml

    directory = Path(__file__).resolve().parents[1] / "scripts" / "simulation_cases"
    for path in directory.glob("*.yaml"):
        case = yaml.safe_load(path.read_text())
        assert case["accepted"]
        assert case["caller"]["facts"]
        assert "accepted" not in case["caller"]
