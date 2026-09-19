import asyncio

import pytest
from pipecat.frames.frames import (
    TextFrame,
    TranscriptionFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.settings import TTSSettings
from pipecat.transcriptions.language import Language

from agent.language import (
    DEFAULT_LANGUAGE,
    CallLanguage,
    LanguageTracker,
    phrases,
    reply_instruction,
)
from tts import language_settings


def heard(language: Language | None) -> TranscriptionFrame:
    return TranscriptionFrame("hello", "caller", "2026-09-19T10:00:00Z", language)


def test_a_call_starts_in_the_clinics_own_language():
    assert CallLanguage().language == DEFAULT_LANGUAGE == Language.ES


def test_one_stray_reading_does_not_move_the_call():
    call = CallLanguage()

    assert call.observe(Language.EN) is False
    assert call.language == Language.ES


def test_two_agreeing_readings_move_the_call():
    call = CallLanguage()

    assert call.observe(Language.EN) is False
    assert call.observe(Language.EN) is True
    assert call.language == Language.EN


def test_going_back_resets_the_run():
    call = CallLanguage()
    call.observe(Language.EN)
    call.observe(Language.ES)

    assert call.observe(Language.EN) is False
    assert call.language == Language.ES


def test_disagreeing_readings_never_accumulate():
    call = CallLanguage()

    assert call.observe(Language.EN) is False
    assert call.observe(Language.FR) is False
    assert call.language == Language.ES


def test_a_missing_reading_is_ignored():
    call = CallLanguage()
    call.observe(Language.EN)

    assert call.observe(None) is False
    assert call.observe(Language.EN) is True


def test_a_regional_tag_is_the_same_call_language():
    call = CallLanguage()

    assert call.observe(Language.ES_ES) is False
    assert call.language == Language.ES


class CapturingTracker(LanguageTracker):
    def __init__(self):
        super().__init__("CA123", CallLanguage())
        self.pushed = []

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.pushed.append(frame)


@pytest.fixture(autouse=True)
def cartesia(monkeypatch):
    """The tracker asks the configured provider how to follow a language."""
    monkeypatch.setenv("TTS_PROVIDER", "cartesia")


def run(tracker: LanguageTracker, frames) -> None:
    async def drive():
        for frame in frames:
            await tracker.process_frame(frame, FrameDirection.DOWNSTREAM)

    asyncio.run(drive())


def test_the_voice_follows_the_caller():
    tracker = CapturingTracker()

    run(tracker, [heard(Language.EN), heard(Language.EN)])

    updates = [f for f in tracker.pushed if isinstance(f, TTSUpdateSettingsFrame)]
    assert len(updates) == 1
    assert updates[0].delta.language == Language.EN


def test_the_update_leads_the_transcript_that_earned_it():
    """The same transcript can end the turn, and the reply must not beat it."""
    tracker = CapturingTracker()
    second = heard(Language.EN)

    run(tracker, [heard(Language.EN), second])

    kinds = [type(frame) for frame in tracker.pushed]
    assert kinds.index(TTSUpdateSettingsFrame) < tracker.pushed.index(second)


def test_the_voice_holds_until_the_readings_agree():
    tracker = CapturingTracker()

    run(tracker, [heard(Language.EN), heard(Language.ES), heard(Language.EN)])

    assert not [f for f in tracker.pushed if isinstance(f, TTSUpdateSettingsFrame)]


def test_every_frame_still_reaches_the_rest_of_the_pipeline():
    tracker = CapturingTracker()
    passing = TextFrame("not a transcript")

    run(tracker, [passing])

    assert passing in tracker.pushed


def test_unwritten_languages_keep_the_clinics_filler_but_their_own_name():
    assert phrases(Language.FR) is phrases(DEFAULT_LANGUAGE)
    assert reply_instruction(Language.FR) == "Reply to the caller in fr."
    assert reply_instruction(Language.EN) == "Reply to the caller in English."


def test_deepgram_follows_a_language_by_swapping_the_voice(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "deepgram")

    assert language_settings(Language.EN) == TTSSettings(voice="aura-2-arcas-en")


def test_deepgram_stays_put_rather_than_reconnecting_for_nothing(monkeypatch):
    """A language setting it ignores would still cost a socket reconnect."""
    monkeypatch.setenv("TTS_PROVIDER", "deepgram")

    assert language_settings(Language.FR) is None
