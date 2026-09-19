"""The language the call is conducted in.

Deepgram tags every transcript with the language it heard, so the signal is
already in the pipeline and costs nothing extra. A single utterance is a weak
read — a Spanish surname inside an English sentence comes back as Spanish — so
the call only moves once AGREEING_TURNS transcripts in a row say the same
thing. The STT itself is never reconfigured: nova-3 `multi` already transcribes
every language below in one stream, and changing its settings would reconnect
the socket mid-call.
"""

import logging
from dataclasses import dataclass

from pipecat.frames.frames import Frame, TranscriptionFrame, TTSUpdateSettingsFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transcriptions.language import Language

from observability import emit
from tts import language_settings

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGE = Language.EN
AGREEING_TURNS = 2


@dataclass(frozen=True)
class Phrases:
    """What the agent says without asking the LLM, and how it names the language."""

    name: str
    greeting: str
    acknowledgement: str
    no_answer: str
    error: str


PHRASES = {
    Language.ES: Phrases(
        name="Spanish",
        greeting="Clínica Arenal, ¿en qué puedo ayudarle?",
        acknowledgement="Un momento, lo consulto.",
        no_answer="Perdone, ¿puede repetirme lo que necesita?",
        error="Lo siento, no he podido procesarlo. ¿Puede repetirlo?",
    ),
    Language.EN: Phrases(
        name="English",
        greeting="Arenal Clinic, how can I help you?",
        acknowledgement="One moment, let me check that.",
        no_answer="Sorry, could you tell me again what you need?",
        error="Sorry, I could not process that. Could you repeat it?",
    ),
}


def phrases(language: Language) -> Phrases:
    """Filler for a language. Anything unwritten falls back to the clinic's own."""
    return PHRASES.get(language, PHRASES[DEFAULT_LANGUAGE])


def reply_instruction(language: Language) -> str:
    written = PHRASES.get(language)
    return f"Reply to the caller in {written.name if written else language.value}."


def _base(language: Language) -> Language:
    """es-ES and es are the same call language."""
    return Language(language.value.split("-")[0])


class CallLanguage:
    """The language of the call so far: one writer, several readers."""

    def __init__(self, language: Language = DEFAULT_LANGUAGE):
        self.language = language
        self._candidate: Language | None = None
        self._agreements = 0

    def observe(self, heard: Language | None) -> bool:
        """Record one transcript's language. True when the call language moved."""
        if heard is None:
            return False

        heard = _base(heard)
        if heard == self.language:
            self._candidate = None
            self._agreements = 0
            return False

        self._agreements = self._agreements + 1 if heard == self._candidate else 1
        self._candidate = heard
        if self._agreements < AGREEING_TURNS:
            return False

        self.language = heard
        self._candidate = None
        self._agreements = 0
        return True


class LanguageTracker(FrameProcessor):
    """Follows the caller's language and moves the voice with it.

    Belongs upstream of the context aggregator, which swallows transcription
    frames rather than forwarding them.
    """

    def __init__(self, call_id: str, language: CallLanguage):
        super().__init__()
        self._call_id = call_id
        self._language = language

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        # The update has to lead the transcript that earned it: this frame can
        # end the turn, and the reply would then be spoken in the old voice.
        if isinstance(frame, TranscriptionFrame) and self._language.observe(
            frame.language
        ):
            await self._follow_the_caller()

        await self.push_frame(frame, direction)

    async def _follow_the_caller(self) -> None:
        language = self._language.language
        logger.info(
            "call language changed | call_id=%s language=%s", self._call_id, language
        )
        emit(self._call_id, "language_changed", {"language": language.value})

        settings = language_settings(language)
        if settings is None:
            return
        await self.push_frame(
            TTSUpdateSettingsFrame(delta=settings), FrameDirection.DOWNSTREAM
        )
