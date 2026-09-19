"""A stand-in for the Prosper platform, so scenarios can run without dialling it.

The clinic is fixed for the whole event and the docs say to cache it, so the
catalogue and the lookup samples here are a snapshot of the real thing. What the
agent submits is kept in memory and read back by the scenario runner: the record
is what the challenge scores, so the record is what this exists to capture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

ACTION_VERBS = {
    "register": "REGISTER",
    "book": "BOOK",
    "reschedule": "RESCHEDULE",
    "cancel": "CANCEL",
    "no-action": "NO_ACTION",
    "escalate": "ESCALATE",
}


def _fixture(name: str) -> dict[str, Any]:
    path = FIXTURES / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def create_app() -> FastAPI:
    app = FastAPI()
    clinic = _fixture("clinic.json")
    directory = _fixture("sample_directory.json").get("matches", [])
    appointments = _fixture("sample_appointments.json")
    availability = _fixture("sample_availability.json")
    submitted: dict[str, list[dict[str, Any]]] = {}

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/api/v1/clinic")
    async def get_clinic() -> dict[str, Any]:
        return clinic

    @app.get("/api/v1/directory")
    async def get_directory(
        name: str | None = None,
        national_id: str | None = None,
        phone: str | None = None,
        date_of_birth: str | None = None,
    ) -> dict[str, Any]:
        # The real endpoint rejects a bare surname, and an agent that only ever
        # sends one would look fine here and fail on the day.
        if name and len(name.split()) < 2:
            return {"matches": []}
        wanted = (name or national_id or phone or date_of_birth or "").lower()
        matches = [
            patient
            for patient in directory
            if wanted
            and any(
                wanted in str(patient.get(field, "")).lower()
                or str(patient.get(field, "")).lower() in wanted
                for field in ("given_name", "first_surname", "national_id", "phone", "date_of_birth")
            )
        ]
        return {"matches": matches}

    @app.get("/api/v1/patients/{patient_id}/appointments")
    async def get_appointments(patient_id: str, when: str = "upcoming") -> dict[str, Any]:
        return appointments

    @app.get("/api/v1/availability")
    async def get_availability() -> dict[str, Any]:
        return availability

    @app.post("/api/v1/submit/{action}")
    async def submit(action: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        call_id = body.pop("call_id", "")
        verb = ACTION_VERBS.get(action, action.upper())
        record = {"action": verb, **({"new_patient": body} if verb == "REGISTER" else body)}
        actions = submitted.setdefault(call_id, [])
        # The real platform answers 409 to an identical action; the record is
        # unchanged either way, which is the part a scenario asserts on.
        if record not in actions:
            actions.append(record)
        return {"call_id": call_id, "record": {"actions": actions}}

    @app.get("/__record/{call_id}")
    async def read_record(call_id: str) -> dict[str, Any]:
        return {"actions": submitted.get(call_id, [])}

    @app.post("/__reset")
    async def reset() -> dict[str, str]:
        submitted.clear()
        return {"status": "reset"}

    return app


app = create_app()
