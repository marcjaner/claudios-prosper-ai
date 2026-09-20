"""Check spoken replies and render calendar dates for the phone call."""

import calendar
import re
from datetime import date

from pipecat.transcriptions.language import Language

from .graph import GRAPH_TOOL_NAMES
from .tools import CLINIC_TOOL_NAMES

MAX_SPOKEN_WORDS = 100
MAX_SPOKEN_CHARACTERS = 800
INTERNAL_SPEECH = re.compile(
    r"\b(?:system prompt|developer instructions?|final outcome already recorded|"
    r"the instruction explicitly states|"
    + "|".join(re.escape(name) for name in (*GRAPH_TOOL_NAMES, *CLINIC_TOOL_NAMES))
    + r")\b|</?(?:think|analysis)>",
    re.IGNORECASE,
)

SPANISH_MONTHS = (
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)
SPANISH_WEEKDAYS = (
    "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo",
)
WEEKDAYS = "|".join((*calendar.day_name, *SPANISH_WEEKDAYS))
ISO_DATE = re.compile(
    rf"\b(?:(?P<weekday>{WEEKDAYS}),?\s+)?(?P<date>\d{{4}}-\d{{2}}-\d{{2}})(?![\dT])",
    re.IGNORECASE,
)


def speech_rejection_reason(text: str) -> str:
    if INTERNAL_SPEECH.search(text):
        return "internal_instructions"
    if len(text) > MAX_SPOKEN_CHARACTERS or len(text.split()) > MAX_SPOKEN_WORDS:
        return "reply_too_long"
    return ""


def spoken_dates(text: str, language: Language) -> str:
    if language not in (Language.EN, Language.ES):
        return text

    def replace(match: re.Match) -> str:
        try:
            value = date.fromisoformat(match["date"])
        except ValueError:
            return match[0]
        if language == Language.ES:
            rendered = f"{value.day} de {SPANISH_MONTHS[value.month]} de {value.year}"
            weekday = SPANISH_WEEKDAYS[value.weekday()]
        else:
            rendered = f"{value.day} {calendar.month_name[value.month]} {value.year}"
            weekday = calendar.day_name[value.weekday()]
        return f"{weekday} {rendered}" if match["weekday"] else rendered

    return ISO_DATE.sub(replace, text)
