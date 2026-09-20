"""Seed a scratch database so the console can be driven without an agent.

`src/agent/` does not exist yet, so nothing writes a patient, an outcome or a
transcript. This writes a handful of calls that look like the eighteen problems
do, into a database of its own:

    uv run python scripts/seed_console.py demo.db
    CALLS_DB=demo.db uv run python -m twilio

It never touches calls.db, and it is a fixture for looking at the console, not
a substitute for the real thing.
"""

import sys
import time
from pathlib import Path

from observability import CallUpdate, Event, Store

HISTORY_CALLS = [
    (
        "Marta Ruiz Gómez",
        "P00042",
        "sanitas",
        "BOOK",
        None,
        "Quería una cita de revisión con dermatología.",
        "Tu cita con la doctora Iglesias queda reservada para el jueves a las diez y media.",
    ),
    (
        "Nuria Sáez Pla",
        "P00913",
        "dkv",
        "RESCHEDULE",
        None,
        "Necesito cambiar mi cita de fisioterapia del martes.",
        "He cambiado la cita al viernes a las doce en Arenal Norte.",
    ),
    (
        "Pilar Vilar Bosch",
        "P00551",
        "mapfre",
        "CANCEL",
        None,
        "Llamo para cancelar la cita de cardiología de mañana.",
        "La cita de mañana ha quedado cancelada.",
    ),
    (
        "Lucía Moreno Gil",
        None,
        "privado",
        "REGISTER",
        None,
        "Soy nueva paciente y quería registrarme en la clínica.",
        "Tu ficha ha quedado creada correctamente, Lucía.",
    ),
    (
        "Álvaro Cid Requena",
        "P00187",
        "adeslas",
        "NO_ACTION",
        "specialty_not_covered",
        "Quería pedir cita para podología con mi seguro.",
        "Adeslas no cubre podología en esta clínica; no he realizado ninguna reserva.",
    ),
    (
        "Tomás Iglesia Font",
        "P01204",
        "asisa",
        "ESCALATE",
        "medical_emergency",
        "Tengo un dolor muy fuerte en el pecho y me cuesta respirar.",
        "Esto puede ser una urgencia médica. Llama al 112 ahora; también aviso al equipo de la clínica.",
    ),
    (
        "Elena Navarro Cruz",
        "P00318",
        "cigna",
        "BOOK",
        None,
        "Busco la primera cita disponible de ginecología.",
        "Te reservo el lunes a las nueve con la doctora Vidal.",
    ),
    (
        "Javier Santos Rey",
        "P00762",
        "axa",
        "BOOK",
        None,
        "Necesito una consulta de traumatología por la rodilla.",
        "Queda reservada una consulta de traumatología el miércoles a las cinco.",
    ),
    (
        "Carmen Vega Soler",
        "P00291",
        "caser",
        "RESCHEDULE",
        None,
        "¿Podemos mover mi revisión dental a primera hora?",
        "Tu revisión dental pasa al jueves a las nueve.",
    ),
    (
        "Daniel Ortega León",
        "P00640",
        "sanitas",
        "BOOK",
        None,
        "Quería una cita de pediatría para mi hijo.",
        "La cita de pediatría queda reservada para mañana a las cuatro y media.",
    ),
    (
        "Irene Molina Paz",
        "P00815",
        "dkv",
        "CANCEL",
        None,
        "Ya no puedo acudir a la consulta de neurología.",
        "He cancelado la consulta de neurología del viernes.",
    ),
    (
        "Sergio Ramos Nieto",
        "P01128",
        "mapfre",
        "NO_ACTION",
        "provider_on_leave",
        "Quería cita esta semana con el doctor Ferrer.",
        "El doctor Ferrer está de permiso esta semana; no he modificado tu agenda.",
    ),
    (
        "Paula Herrera Díaz",
        "P00473",
        "adeslas",
        "BOOK",
        None,
        "Necesito una revisión de oftalmología por la tarde.",
        "Te reservo oftalmología el martes a las seis.",
    ),
    (
        "Marcos Gil Pardo",
        "P01054",
        "asisa",
        "RESCHEDULE",
        None,
        "Tengo que retrasar mi análisis de sangre.",
        "El análisis queda cambiado al lunes a las ocho y cuarto.",
    ),
    (
        "Sara Domínguez Cano",
        None,
        "privado",
        "REGISTER",
        None,
        "Nunca he ido a la clínica y necesito darme de alta.",
        "Tu registro como nueva paciente se ha completado.",
    ),
    (
        "Andrés Fuentes Mora",
        "P01309",
        "cigna",
        "BOOK",
        None,
        "Quería pedir una consulta de digestivo.",
        "Tienes consulta de digestivo el viernes a las once y media.",
    ),
    (
        "Beatriz Lozano Ríos",
        "P01422",
        "axa",
        "NO_ACTION",
        "referral_required",
        "Necesito una cita de rehabilitación.",
        "Para rehabilitación necesitamos primero una derivación; no he creado ninguna cita.",
    ),
    (
        "Raúl Méndez Sanz",
        "P01577",
        "caser",
        "BOOK",
        None,
        "¿Hay algún hueco para una revisión de urología?",
        "He reservado urología el miércoles a las doce y cuarto.",
    ),
    (
        "Clara Vidal Martín",
        "P01635",
        "sanitas",
        "CANCEL",
        None,
        "Quiero anular mi sesión de fisioterapia del lunes.",
        "La sesión del lunes ha quedado anulada.",
    ),
    (
        "Hugo Prieto Lara",
        "P01741",
        "dkv",
        "BOOK",
        None,
        "Me gustaría reservar una consulta de alergología.",
        "Tu consulta de alergología queda para el jueves a las cuatro.",
    ),
    (
        "Rocío Cabrera Núñez",
        "P01826",
        "mapfre",
        "NO_ACTION",
        "no_availability",
        "Necesito dermatología antes del viernes.",
        "No hay disponibilidad de dermatología antes del viernes; no he realizado cambios.",
    ),
    (
        "Iván Blanco Soto",
        "P01903",
        "adeslas",
        "BOOK",
        None,
        "Quería una revisión anual de cardiología.",
        "La revisión queda reservada para el martes a las diez.",
    ),
    (
        "Ana Pastor Beltrán",
        "P02018",
        "asisa",
        "RESCHEDULE",
        None,
        "Quiero cambiar mi cita de ginecología a la próxima semana.",
        "He movido la cita al próximo lunes a las cinco y media.",
    ),
    (
        "Miguel Torres Calvo",
        "P02144",
        "cigna",
        "ESCALATE",
        "caller_not_authorised",
        "Quiero cambiar la cita de mi hermana.",
        "Necesito autorización de la paciente para modificar esa cita; lo traslado al equipo de recepción.",
    ),
]


def conversation(
    name: str, patient_id: str | None, outcome: str, request: str, answer: str
) -> list[tuple[str, dict]]:
    first_name = name.split()[0]
    events = [
        ("tts", {"text": "Clínica Arenal, buenos días. ¿En qué puedo ayudarle?"}),
        ("stt_final", {"text": request}),
        (
            "tool_call",
            {
                "name": "search_patients",
                "tool_call_id": "identify",
                "arguments": {"name": name},
            },
        ),
        (
            "tool_result",
            {
                "name": "search_patients",
                "tool_call_id": "identify",
                "status": 200,
                "ms": 128,
                "result": {"patient_id": patient_id, "name": name}
                if patient_id
                else {"matches": []},
            },
        ),
        ("tts", {"text": f"Gracias, {first_name}. Déjame comprobarlo."}),
    ]
    action = {
        "BOOK": "book_appointment",
        "RESCHEDULE": "reschedule_appointment",
        "CANCEL": "cancel_appointment",
        "REGISTER": "register_patient",
        "NO_ACTION": "submit_no_action",
        "ESCALATE": "escalate_to_human",
    }[outcome]
    events.extend(
        [
            (
                "tool_call",
                {
                    "name": action,
                    "tool_call_id": "action",
                    "arguments": {"patient_id": patient_id},
                },
            ),
            (
                "tool_result",
                {
                    "name": action,
                    "tool_call_id": "action",
                    "status": 200,
                    "ms": 246,
                    "result": {"ok": True},
                },
            ),
            ("tts", {"text": answer}),
            ("stt_final", {"text": "Perfecto, muchas gracias por la ayuda."}),
            (
                "tts",
                {
                    "text": "Gracias por llamar a Clínica Arenal. Que tengas un buen día."
                },
            ),
        ]
    )
    return events


def seed(path: Path) -> None:
    store = Store(path)
    now = time.time()
    batch = []

    # Six calls with a full transcript, for the call view.
    for index, (
        name,
        patient_id,
        insurer,
        outcome,
        reason,
        request,
        answer,
    ) in enumerate(HISTORY_CALLS):
        call_id = f"CAdemo{index:025d}"
        started = now - 420 - index * 390
        duration = 64 + index % 7 * 8
        batch.append(
            CallUpdate(
                call_id,
                {
                    "started_at": started,
                    "ended_at": started + duration,
                    "state": "ended",
                    "from_number": f"+34612{index:06d}",
                    "patient_id": patient_id,
                    "patient_name": name,
                    "insurer": insurer,
                    "outcome": outcome,
                    "reason": reason,
                    "ttfa_seconds": round(0.34 + index % 6 * 0.06, 2),
                    "cost_eur": round(0.0138 + index % 8 * 0.0017, 4),
                },
            )
        )
        turns = conversation(name, patient_id, outcome, request, answer)
        spacing = (duration - 8) / max(1, len(turns) - 1)
        for turn, (kind, payload) in enumerate(turns):
            batch.append(Event(call_id, started + 3 + turn * spacing, kind, payload))

    # Volume behind them, so the histogram has a shape to read.
    store.write(batch)
    store.close()
    print(f"seeded {len(HISTORY_CALLS)} calls into {path}")


if __name__ == "__main__":
    seed(Path(sys.argv[1] if len(sys.argv) > 1 else "demo.db"))
