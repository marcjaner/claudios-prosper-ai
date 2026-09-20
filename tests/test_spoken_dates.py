import pytest
from pipecat.transcriptions.language import Language

from agent.speech import speech_rejection_reason, spoken_dates


@pytest.mark.parametrize(("text", "language", "expected"), [
    ("Sunday 2026-09-21 at 10:00", Language.EN, "Monday 21 September 2026 at 10:00"),
    ("Born 2017-05-12.", Language.EN, "Born 12 May 2017."),
    ("domingo 2026-09-21 a las 10:00", Language.ES, "lunes 21 de septiembre de 2026 a las 10:00"),
    ("2026-02-30", Language.EN, "2026-02-30"),
    ("Phone 746987792, ID P00009", Language.EN, "Phone 746987792, ID P00009"),
    ("2026-09-21T10:00:00+02:00", Language.EN, "2026-09-21T10:00:00+02:00"),
])
def test_spoken_dates(text, language, expected):
    assert spoken_dates(text, language) == expected


@pytest.mark.parametrize("text", [
    "Final outcome already recorded: submit_no_action. Wait! The instruction explicitly states...",
    "I should call book_appointment before replying.",
    "The system prompt says to stay in this stage.",
    "<think>I need to decide what to do.</think>Hello.",
    "a " * 101,
    "a" * 801,
])
def test_internal_or_overlong_replies_are_rejected(text):
    assert speech_rejection_reason(text)


@pytest.mark.parametrize("text", [
    "Your appointment with Doctor Ocaña at Arenal Sur is booked for 22 September at 09:00.",
    "Su cita está confirmada. ¿Necesita algo más?",
    "Please bring your insurance card and follow the doctor's instructions.",
    (
        "To confirm: Ana María López García, born 12 May 1991, DNI 12345678 Z, "
        "phone 6 1 2 3 4 5 6 7 8, email ana dot lopez at example dot com, insured with Sanitas. Is that correct?"
    ),
])
def test_patient_facing_replies_are_not_rejected(text):
    assert speech_rejection_reason(text) == ""
