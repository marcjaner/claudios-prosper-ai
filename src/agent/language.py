"""Choose reply language from complete caller turns, preserving it for names and IDs."""

import re
import unicodedata
from dataclasses import dataclass

from pipecat.transcriptions.language import Language

DEFAULT_LANGUAGE = Language.EN


@dataclass(frozen=True)
class Phrases:
    """What the agent says without asking the LLM, and how it names the language."""

    name: str
    greeting: str
    acknowledgement: str
    no_answer: str
    error: str
    escalated: str


PHRASES = {
    Language.CA: Phrases(
        name="Catalan",
        greeting="Clínica Arenal, en què us puc ajudar?",
        acknowledgement="Un moment, ho consulto.",
        no_answer="Perdoneu, podeu repetir què necessiteu?",
        error="Ho sento, no ho he pogut processar. Ho podeu repetir?",
        escalated=(
            "Passo la vostra sol·licitud a un company del centre, que us trucarà "
            "en breu. Gràcies per la vostra trucada."
        ),
    ),
    Language.ES: Phrases(
        name="Spanish",
        greeting="Clínica Arenal, ¿en qué puedo ayudarle?",
        acknowledgement="Un momento, lo consulto.",
        no_answer="Perdone, ¿puede repetirme lo que necesita?",
        error="Lo siento, no he podido procesarlo. ¿Puede repetirlo?",
        escalated=(
            "Voy a pasar su solicitud a un compañero del centro, que le llamará "
            "en breve. Gracias por su llamada."
        ),
    ),
    Language.EN: Phrases(
        name="English",
        greeting="Arenal Clinic, how can I help you?",
        acknowledgement="One moment, let me check that.",
        no_answer="Sorry, could you tell me again what you need?",
        error="Sorry, I could not process that. Could you repeat it?",
        escalated=(
            "I am passing your request to a member of our staff, who will call "
            "you back shortly. Thank you for calling."
        ),
    ),
}


def phrases(language: Language) -> Phrases:
    """Filler for a language. Anything unwritten falls back to the clinic's own."""
    return PHRASES.get(language, PHRASES[DEFAULT_LANGUAGE])


def reply_instruction(language: Language) -> str:
    written = PHRASES.get(language)
    return f"Reply to the caller in {written.name if written else language.value}."


LANGUAGE_WORDS = {
    Language.EN: {
        "i",
        "my",
        "me",
        "am",
        "is",
        "are",
        "have",
        "want",
        "need",
        "would",
        "like",
        "appointment",
        "please",
        "could",
        "can",
        "speak",
        "english",
        "the",
        "with",
        "for",
        "and",
        "that",
        "this",
        "you",
        "your",
        "birth",
        "book",
        "works",
        "tomorrow",
        "thanks",
        "hello",
        "yes",
    },
    Language.ES: {
        "perdone",
        "acabo",
        "entender",
        "si",
        "dia",
        "yo",
        "mi",
        "soy",
        "estoy",
        "tengo",
        "quiero",
        "queria",
        "necesito",
        "cita",
        "quisiera",
        "puedo",
        "puede",
        "podemos",
        "hablar",
        "espanol",
        "castellano",
        "para",
        "una",
        "un",
        "con",
        "por",
        "favor",
        "que",
        "me",
        "la",
        "el",
        "usted",
        "fecha",
        "nacimiento",
        "reservar",
        "gracias",
        "buenos",
        "buenas",
        "hola",
        "manana",
    },
    Language.CA: {
        "soc",
        "estic",
        "tinc",
        "vull",
        "voldria",
        "necessito",
        "visita",
        "demanar",
        "hora",
        "metge",
        "metgessa",
        "puc",
        "podeu",
        "podem",
        "parlar",
        "catala",
        "sisplau",
        "gracies",
        "bon",
        "bona",
        "dia",
        "tarda",
        "naixement",
        "per",
        "una",
        "amb",
        "em",
        "que",
        "el",
        "la",
        "si",
        "us",
        "plau",
        "dema",
    },
}
LANGUAGE_NAMES = {
    "english": Language.EN,
    "ingles": Language.EN,
    "angles": Language.EN,
    "spanish": Language.ES,
    "espanol": Language.ES,
    "castellano": Language.ES,
    "catalan": Language.CA,
    "catala": Language.CA,
}


def _normalize(text: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(c)
    )


def _requested_language(text: str) -> Language | None:
    # A doctor-language requirement is separate from the conversation language.
    matches = list(
        re.finditer(
            r"(?:speak|speaking|switch to|continue in|hablar|hablamos|parlar|parlem)\s+(?:(?:in|en)\s+)?"
            r"(english|ingles|angles|spanish|espanol|castellano|catalan|catala)\b",
            text,
        )
    )
    for match in reversed(matches):
        prefix = re.split(r"[.!?,;]", text[: match.start()])[-1]
        if re.search(
            r"\b(doctor|provider|medico|metge|metgessa|don't|do not|cannot|can't|not|no|neither|ni)\b",
            prefix,
        ):
            continue
        return LANGUAGE_NAMES[match[1]]
    return None


class CallLanguage:
    def __init__(self, language: Language = DEFAULT_LANGUAGE):
        self.language = language

    def observe_turn(self, text: str) -> bool:
        normalized = _normalize(text)
        requested = _requested_language(normalized)
        if requested is None:
            words = set(re.findall(r"[a-z]+", normalized))
            if (
                "hola" in words
                and self.language == Language.EN
                and not (
                    words & (LANGUAGE_WORDS[Language.EN] - LANGUAGE_WORDS[Language.ES])
                )
            ):
                self.language = Language.ES
                return True
            scores = {
                language: len(words & markers)
                for language, markers in LANGUAGE_WORDS.items()
            }
            ranked = sorted(scores, key=lambda language: scores[language], reverse=True)
            if scores[ranked[0]] < 2 or scores[ranked[0]] == scores[ranked[1]]:
                return False
            requested = ranked[0]
        if requested == self.language:
            return False
        self.language = requested
        return True
