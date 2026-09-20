"""Clinic staff steering the agent from the call detail while the call is live."""

import asyncio
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

import observability.api as api_module
from agent.agent import _graph_prompt
from agent.stage_runtime import CallGraph
from observability.api import register_dashboard
from storage import CallRepository, Database
from storage.repository import STAFF_INSTRUCTION_EVENT

CALL_ID = "CA-live"
INSTRUCTION = "Only offer afternoon slots"


class FakeStore:
    def __init__(self, state="thinking"):
        self.state = state

    def get_call(self, call_id):
        return {"call_id": call_id, "state": self.state}


def build_client(monkeypatch, tmp_path, *, active=True, store=None):
    emitted = []
    monkeypatch.setattr(
        api_module, "emit", lambda call_id, kind, payload=None: emitted.append((kind, payload))
    )
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'agent.db'}")
    repository = CallRepository(database)

    async def prepare():
        await database.init()
        await repository.create_call(CALL_ID, None, datetime.now(UTC))

    asyncio.run(prepare())
    app = FastAPI()
    app.state.active_calls = {CALL_ID: object()} if active else {}
    app.state.guardrail_repository = repository
    app.state.store = store or FakeStore()
    register_dashboard(app)
    return TestClient(app), repository, emitted


def instructions_of(repository):
    return asyncio.run(repository.staff_instructions(CALL_ID))


def test_an_instruction_is_stored_for_the_agent_and_shown_in_the_transcript(
    monkeypatch, tmp_path
):
    client, repository, emitted = build_client(monkeypatch, tmp_path)

    response = client.post(f"/api/calls/{CALL_ID}/instructions", json={"text": f"  {INSTRUCTION} "})

    assert response.status_code == 200
    assert response.json() == {"status": "queued", "call_id": CALL_ID}
    assert instructions_of(repository) == [INSTRUCTION]
    assert emitted == [(STAFF_INSTRUCTION_EVENT, {"text": INSTRUCTION})]


def test_instructions_accumulate_in_order(monkeypatch, tmp_path):
    client, repository, _ = build_client(monkeypatch, tmp_path)

    client.post(f"/api/calls/{CALL_ID}/instructions", json={"text": "first"})
    client.post(f"/api/calls/{CALL_ID}/instructions", json={"text": "second"})

    assert instructions_of(repository) == ["first", "second"]


def test_a_blank_instruction_is_rejected(monkeypatch, tmp_path):
    client, repository, emitted = build_client(monkeypatch, tmp_path)

    assert client.post(f"/api/calls/{CALL_ID}/instructions", json={"text": "  "}).status_code == 422
    assert client.post(f"/api/calls/{CALL_ID}/instructions", json={}).status_code == 422
    assert instructions_of(repository) == [] and emitted == []


def test_a_finished_call_takes_no_instructions(monkeypatch, tmp_path):
    client, repository, emitted = build_client(monkeypatch, tmp_path, active=False)

    response = client.post(f"/api/calls/{CALL_ID}/instructions", json={"text": INSTRUCTION})

    assert response.status_code == 409
    assert instructions_of(repository) == [] and emitted == []


def test_a_call_held_by_staff_takes_no_instructions(monkeypatch, tmp_path):
    client, _, emitted = build_client(monkeypatch, tmp_path, store=FakeStore(state="operator"))

    response = client.post(f"/api/calls/{CALL_ID}/instructions", json={"text": INSTRUCTION})

    assert response.status_code == 409 and emitted == []


def test_the_prompt_carries_staff_instructions_as_authoritative():
    state = CallGraph.start()

    with_staff = _graph_prompt(state, staff_instructions=f"- {INSTRUCTION}")
    without_staff = _graph_prompt(state)

    assert "Instructions from clinic staff" in with_staff
    assert f"- {INSTRUCTION}" in with_staff
    assert "authoritative" in with_staff
    assert "clinic staff" not in without_staff
