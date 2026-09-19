"""Seed a scratch database so the console can be driven without an agent.

`src/agent/` does not exist yet, so nothing writes a patient, an outcome or a
transcript. This writes a handful of calls that look like the eighteen problems
do, into a database of its own:

    uv run python scripts/seed_console.py demo.db
    CALLS_DB=demo.db uv run python -m twilio

It never touches calls.db, and it is a fixture for looking at the console, not
a substitute for the real thing.
"""

import random
import sys
import time
from pathlib import Path

from observability import CallUpdate, Event, Store

TURNS = [
    ("tts", "Clínica Arenal, buenos días. ¿En qué puedo ayudarle?"),
    ("stt_final", "Hola, buenos días. Quería pedir cita con la dermatóloga."),
    (
        "tool_call",
        {
            "name": "search_patient",
            "tool_call_id": "t1",
            "url": "/api/v1/directory",
            "request": {"name": "Marta Ruiz", "phone": "612345678"},
        },
    ),
    (
        "tool_result",
        {
            "name": "search_patient",
            "tool_call_id": "t1",
            "status": 200,
            "ms": 142,
            "response": {
                "matches": [
                    {
                        "patient_id": "P00042",
                        "insurer": "sanitas",
                        "has_visited_before": True,
                    }
                ]
            },
        },
    ),
    ("llm", "Una coincidencia, paciente recurrente. Busco hueco de revisión."),
    (
        "tool_call",
        {
            "name": "search_availability",
            "tool_call_id": "t2",
            "url": "/api/v1/availability",
            "request": {"specialty_id": "dermatology", "patient_id": "P00042"},
        },
    ),
    (
        "tool_result",
        {
            "name": "search_availability",
            "tool_call_id": "t2",
            "status": 200,
            "ms": 310,
            "response": {
                "appointment_type": {
                    "id": "dermatology_review",
                    "duration_minutes": 20,
                },
                "slots": 14,
                "blocked": ["Dra. Iglesias no acepta DKV"],
            },
        },
    ),
    (
        "tts",
        "Perfecto, Marta. Con la doctora Iglesias tengo el jueves a las diez y media.",
    ),
    ("stt_final", "El jueves me viene bien, sí."),
    ("tts", "Hecho. Jueves 24 a las diez y media en Arenal Norte."),
    (
        "submit",
        {
            "route": "/api/v1/submit/book",
            "status": 200,
            "request": {
                "patient_id": "P00042",
                "provider_id": "PR05",
                "location_id": "norte",
                "slot": "2026-09-24T10:30:00+02:00",
                "policy_id": "sanitas",
            },
        },
    ),
]

INSURERS = ("sanitas", "adeslas", "dkv", "asisa", "mapfre", "caser", "cigna", "axa")
REASONS = (
    "no_availability",
    "specialty_not_covered",
    "provider_on_leave",
    "out_of_scope",
    "medical_emergency",
    "patient_not_found",
)

CALLS = [
    ("Marta Ruiz Gómez", "P00042", "sanitas", "BOOK", None),
    ("Álvaro Cid Requena", "P00187", "adeslas", "NO_ACTION", "specialty_not_covered"),
    ("Nuria Sáez Pla", "P00913", "dkv", "RESCHEDULE", None),
    ("Tomás Iglesia Font", "P01204", "asisa", "ESCALATE", "medical_emergency"),
    ("Pilar Vilar Bosch", "P00551", "mapfre", "CANCEL", None),
    ("Jordi Boix Serra", None, "privado", "NO_ACTION", "patient_not_found"),
]


# The shape a scored run leaves behind: mostly writes, a steady minority of
# reasoned closes, and a few calls that ended with nothing submitted.
MIX = (
    (["BOOK"] * 5)
    + ["RESCHEDULE", "CANCEL", "REGISTER"]
    + ["NO_ACTION"] * 2
    + ["ESCALATE", None]
)
BACKGROUND_CALLS = 140
BACKGROUND_SPAN_SECONDS = 5 * 3600


def seed(path: Path) -> None:
    store = Store(path)
    now = time.time()
    batch = []

    # Six calls with a full transcript, for the call view.
    for index, (name, patient_id, insurer, outcome, reason) in enumerate(CALLS):
        call_id = f"CAseed{index:026d}"
        started = now - (index + 1) * 900
        batch.append(
            CallUpdate(
                call_id,
                {
                    "started_at": started,
                    "ended_at": started + 70 + index * 9,
                    "state": "ended",
                    "from_number": "+34612345678" if index % 3 else None,
                    "patient_id": patient_id,
                    "patient_name": name,
                    "insurer": insurer,
                    "outcome": outcome,
                    "reason": reason,
                    "ttfa_seconds": round(0.32 + index * 0.07, 3),
                    "cost_eur": round(0.0121 + index * 0.0018, 4),
                },
            )
        )
        for turn, (kind, payload) in enumerate(TURNS):
            batch.append(
                Event(
                    call_id,
                    started + 2 + turn * 5.5,
                    kind,
                    {"text": payload} if isinstance(payload, str) else payload,
                )
            )

    # Volume behind them, so the histogram has a shape to read.
    rng = random.Random(20260920)
    for index in range(BACKGROUND_CALLS):
        outcome = rng.choice(MIX)
        # Bunched rather than evenly spread: a Run All dials ten at a time.
        started = now - rng.triangular(
            0, BACKGROUND_SPAN_SECONDS, BACKGROUND_SPAN_SECONDS * 0.3
        )
        batch.append(
            CallUpdate(
                f"CAbulk{index:026d}",
                {
                    "started_at": started,
                    "ended_at": started + rng.uniform(35, 160),
                    "state": "ended",
                    "from_number": "+34612345678",
                    "insurer": rng.choice(INSURERS),
                    "outcome": outcome,
                    "reason": rng.choice(REASONS)
                    if outcome in ("NO_ACTION", "ESCALATE")
                    else None,
                    "ttfa_seconds": round(rng.uniform(0.28, 0.95), 3),
                    "cost_eur": round(rng.uniform(0.009, 0.031), 4),
                    "error": "caller hung up"
                    if outcome is None and rng.random() < 0.5
                    else None,
                },
            )
        )

    store.write(batch)
    store.close()
    print(f"seeded {len(CALLS) + BACKGROUND_CALLS} calls into {path}")


if __name__ == "__main__":
    seed(Path(sys.argv[1] if len(sys.argv) > 1 else "demo.db"))
