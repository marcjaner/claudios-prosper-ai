"""Clinic staff escalating a live call from the console."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipecat.frames.frames import EndFrame, TTSSpeakFrame
from pipecat.transcriptions.language import Language

import agent.operator as operator_module
from agent.clinic_api import ClinicApi, ProsperApiError
from agent.language import phrases
from agent.operator import register_operator_api
from observability.api import mount_dashboard, register_dashboard

CALL_ID = "CA-live"


class FakeWorker:
    def __init__(self):
        self.frames = []

    async def queue_frames(self, frames):
        self.frames.extend(frames)


class FakeProsper:
    def __init__(self, error: ProsperApiError | None = None):
        self.requests = []
        self.error = error

    def escalate(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return {"status": "ok", "action": "ESCALATE"}


class FakeRepository:
    def __init__(self):
        self.submissions = []

    async def record_submission(self, call_id, action, request, status, response):
        self.submissions.append((call_id, action, request, status, response))


class FakeStore:
    def __init__(self, events=()):
        self.events = list(events)

    def get_events(self, call_id):
        return self.events


def build_client(monkeypatch, prosper, store=None, worker=None):
    monkeypatch.setattr(ClinicApi, "from_environment", classmethod(lambda cls: prosper))
    emitted, updates = [], []
    monkeypatch.setattr(
        operator_module, "emit", lambda call_id, kind, payload=None: emitted.append((kind, payload))
    )
    monkeypatch.setattr(
        operator_module, "update_call", lambda call_id, **fields: updates.append(fields)
    )
    app = FastAPI()
    app.state.active_workers = {CALL_ID: worker} if worker else {}
    app.state.guardrail_repository = FakeRepository()
    app.state.store = store or FakeStore()
    register_operator_api(app)
    return TestClient(app), app, emitted, updates


def test_escalation_records_the_outcome_then_says_goodbye_and_hangs_up(monkeypatch):
    worker = FakeWorker()
    prosper = FakeProsper()
    spoke_spanish = FakeStore([
        {"id": 1, "ts": 1.0, "kind": "stt_final", "payload": {"text": "hola"}},
        {"id": 2, "ts": 1.1, "kind": "language_changed", "payload": {"language": "es"}},
    ])
    client, app, emitted, updates = build_client(monkeypatch, prosper, spoke_spanish, worker)

    response = client.post(f"/api/calls/{CALL_ID}/escalate")

    assert response.status_code == 200
    assert response.json() == {"status": "escalated", "call_id": CALL_ID}

    [request] = prosper.requests
    assert (request.call_id, request.reason.value) == (CALL_ID, "out_of_scope")

    # The same trail the agent leaves, so the dashboard needs nothing new.
    kinds = [kind for kind, _ in emitted]
    assert kinds == ["operator_escalate", "tool_call", "tool_result", "submit", "tts"]
    by_kind = dict(emitted)
    assert by_kind["tool_call"]["name"] == "escalate_to_human"
    assert by_kind["tool_call"]["tool_call_id"] == by_kind["tool_result"]["tool_call_id"]
    assert by_kind["submit"]["route"] == "/api/v1/submit/escalate"
    assert updates == [{"outcome": "ESCALATE", "reason": "out_of_scope", "state": "stopping"}]
    assert app.state.guardrail_repository.submissions[0][:2] == (CALL_ID, "ESCALATE")

    farewell = phrases(Language.ES).escalated
    assert by_kind["tts"] == {"text": farewell}
    speak, end = worker.frames
    assert isinstance(speak, TTSSpeakFrame) and speak.text == farewell
    assert speak.append_to_context is False
    assert isinstance(end, EndFrame)


def test_farewell_defaults_to_the_agent_language_when_the_caller_never_switched(monkeypatch):
    worker = FakeWorker()
    client, _, emitted, _ = build_client(monkeypatch, FakeProsper(), FakeStore(), worker)

    assert client.post(f"/api/calls/{CALL_ID}/escalate").status_code == 200
    assert dict(emitted)["tts"] == {"text": phrases(Language.EN).escalated}


def test_a_finished_call_cannot_be_escalated(monkeypatch):
    prosper = FakeProsper()
    client, _, emitted, _ = build_client(monkeypatch, prosper)

    response = client.post(f"/api/calls/{CALL_ID}/escalate")

    assert response.status_code == 409
    assert prosper.requests == []
    assert emitted == []


def test_a_rejected_submission_leaves_the_call_running(monkeypatch):
    worker = FakeWorker()
    prosper = FakeProsper(error=ProsperApiError(422, {"detail": "call not found"}))
    client, app, emitted, updates = build_client(monkeypatch, prosper, worker=worker)

    response = client.post(f"/api/calls/{CALL_ID}/escalate")

    assert response.status_code == 502
    assert "call not found" in response.json()["detail"]
    assert worker.frames == []
    assert emitted == [] and updates == []
    assert app.state.guardrail_repository.submissions == []


def test_routes_added_before_the_dashboard_mount_stay_reachable(monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    monkeypatch.setattr("observability.api.DASHBOARD_DIST", tmp_path)

    app = FastAPI()
    app.state.store = FakeStore()
    app.state.active_workers = {}
    register_dashboard(app)
    register_operator_api(app)
    mount_dashboard(app)
    client = TestClient(app)

    assert client.post(f"/api/calls/{CALL_ID}/escalate").status_code == 409
    assert client.get("/").status_code == 200
