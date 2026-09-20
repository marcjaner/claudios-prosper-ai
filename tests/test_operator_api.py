"""Clinic staff taking a live call over from the console, with the pipeline faked."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.transcriptions.language import Language
from starlette.websockets import WebSocketDisconnect

import agent.operator as operator_module
from agent.clinic_api import ClinicApi, ProsperApiError
from agent.language import phrases
from agent.operator import CALL_NOT_ACTIVE, LINE_OPEN, register_operator_api
from observability.api import mount_dashboard, register_dashboard
from twilio.transport import ActiveCall

CALL_ID = "CA-live"
CALLER_SAYS = b"\x01\x02" * 160
OPERATOR_SAYS = b"\x09\x09" * 160


class FakeWorker:
    def __init__(self):
        self.frames = []
        self.cancelled = None

    async def queue_frame(self, frame):
        self.frames.append(frame)

    async def cancel(self, reason=None):
        self.cancelled = reason


class FakeBridge:
    def __init__(self):
        self.active = False
        self.waited_for_handoff = None
        self.caller_audio = asyncio.Queue()
        self.spoken = []

    async def take_over(self, *, after_handoff=False):
        self.waited_for_handoff = after_handoff
        self.active = True
        self.caller_audio.put_nowait(CALLER_SAYS)

    async def speak(self, pcm):
        self.spoken.append(pcm)


class FakeProsper:
    def __init__(self, error: ProsperApiError | None = None):
        self.requests = []
        self.error = error

    def escalate(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return {"status": "ok", "action": "ESCALATE"}

    def close(self):
        pass


class FakeRepository:
    def __init__(self):
        self.submissions = []

    async def record_submission(self, call_id, action, request, status, response):
        self.submissions.append((call_id, action, request, status, response))


class FakeStore:
    def __init__(self, events=(), outcome=None):
        self.events = list(events)
        self.outcome = outcome

    def get_events(self, call_id):
        return self.events

    def get_call(self, call_id):
        return {"call_id": call_id, "outcome": self.outcome}


def build_client(monkeypatch, prosper, store=None, active=None):
    monkeypatch.setattr(ClinicApi, "from_environment", classmethod(lambda cls: prosper))
    emitted, updates = [], []
    monkeypatch.setattr(
        operator_module,
        "emit",
        lambda call_id, kind, payload=None: emitted.append((kind, payload)),
    )
    monkeypatch.setattr(
        operator_module, "update_call", lambda call_id, **fields: updates.append(fields)
    )
    app = FastAPI()
    app.state.active_calls = {CALL_ID: active} if active else {}
    app.state.guardrail_repository = FakeRepository()
    app.state.store = store or FakeStore()
    register_operator_api(app)
    return TestClient(app), app, emitted, updates


def connect_rejected(client) -> WebSocketDisconnect:
    with pytest.raises(WebSocketDisconnect) as closed:  # noqa: SIM117
        with client.websocket_connect(f"/api/calls/{CALL_ID}/operator"):
            pass
    return closed.value


def take_over(client):
    with client.websocket_connect(f"/api/calls/{CALL_ID}/operator") as line:
        assert line.receive_text() == LINE_OPEN
        heard = line.receive_bytes()
        line.send_bytes(OPERATOR_SAYS)
    return heard


def test_taking_over_hands_off_in_the_caller_language_then_bridges_audio(monkeypatch):
    active = ActiveCall(FakeWorker(), FakeBridge())
    prosper = FakeProsper()
    spoke_spanish = FakeStore(
        [
            {"id": 1, "ts": 1.0, "kind": "stt_final", "payload": {"text": "hola"}},
            {
                "id": 2,
                "ts": 1.1,
                "kind": "language_changed",
                "payload": {"language": "es"},
            },
        ]
    )
    client, app, emitted, updates = build_client(
        monkeypatch, prosper, spoke_spanish, active
    )

    heard = take_over(client)

    assert heard == CALLER_SAYS
    assert active.bridge.spoken == [OPERATOR_SAYS]
    assert active.bridge.waited_for_handoff is True
    assert active.worker.cancelled == "operator hung up"

    handoff = phrases(Language.ES).handoff
    [speak] = active.worker.frames
    assert isinstance(speak, TTSSpeakFrame) and speak.text == handoff
    assert speak.append_to_context is False

    [request] = prosper.requests
    assert (request.call_id, request.reason.value) == (CALL_ID, "out_of_scope")
    by_kind = dict(emitted)
    assert by_kind["tts"] == {"text": handoff}
    assert by_kind["tool_call"]["name"] == "escalate_to_human"
    assert (
        by_kind["tool_call"]["tool_call_id"] == by_kind["tool_result"]["tool_call_id"]
    )
    assert by_kind["submit"]["route"] == "/api/v1/submit/escalate"
    assert "operator_takeover" in by_kind and "operator_hangup" in by_kind
    assert {"state": "operator", "handled_by": "operator"} in updates
    assert {"outcome": "ESCALATE", "reason": "out_of_scope"} in updates
    assert app.state.guardrail_repository.submissions[0][:2] == (CALL_ID, "ESCALATE")


def test_handoff_defaults_to_the_agent_language_when_the_caller_never_switched(
    monkeypatch,
):
    active = ActiveCall(FakeWorker(), FakeBridge())
    client, _, emitted, _ = build_client(
        monkeypatch, FakeProsper(), FakeStore(), active
    )

    take_over(client)

    assert dict(emitted)["tts"] == {"text": phrases(Language.EN).handoff}


def test_a_call_with_an_outcome_is_not_escalated_again(monkeypatch):
    active = ActiveCall(FakeWorker(), FakeBridge())
    prosper = FakeProsper()
    client, _, _, _ = build_client(
        monkeypatch, prosper, FakeStore(outcome="BOOK"), active
    )

    take_over(client)

    assert prosper.requests == []


def test_a_finished_call_cannot_be_taken_over(monkeypatch):
    prosper = FakeProsper()
    client, _, emitted, _ = build_client(monkeypatch, prosper)

    closed = connect_rejected(client)

    assert closed.code == CALL_NOT_ACTIVE
    assert prosper.requests == [] and emitted == []


def test_a_rejected_submission_keeps_the_operator_on_the_line(monkeypatch):
    active = ActiveCall(FakeWorker(), FakeBridge())
    prosper = FakeProsper(error=ProsperApiError(422, {"detail": "call not found"}))
    client, app, emitted, updates = build_client(monkeypatch, prosper, active=active)

    heard = take_over(client)

    assert heard == CALLER_SAYS
    by_kind = dict(emitted)
    assert "call not found" in by_kind["error"]["message"]
    assert "submit" not in by_kind
    assert not any("outcome" in update for update in updates)
    assert app.state.guardrail_repository.submissions == []


def test_routes_added_before_the_dashboard_mount_stay_reachable(monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    monkeypatch.setattr("observability.api.DASHBOARD_DIST", tmp_path)

    app = FastAPI()
    app.state.store = FakeStore()
    app.state.active_calls = {}
    register_dashboard(app)
    register_operator_api(app)
    mount_dashboard(app)
    client = TestClient(app)

    assert connect_rejected(client).code == CALL_NOT_ACTIVE
    assert client.get("/").status_code == 200
