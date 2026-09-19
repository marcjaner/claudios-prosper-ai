import asyncio
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from pipecat.frames.frames import InterimTranscriptionFrame, TTSSpeakFrame
from pipecat.processors.aggregators.llm_response_universal import (
    UserTurnStoppedMessage,
)
from pipecat.processors.frame_processor import FrameDirection

import observability
from agent import transcription
from agent.transcription import (
    EMPTY_TURN_RECOVERY_MESSAGE,
    TranscriptObserver,
    create_user_aggregator,
)
from observability.frames import TTSRequestedFrame
from storage import Database
from twilio import CallMeta


class FakeBus:
    def __init__(self):
        self.events = []
        self.updates = []

    def emit(self, call_id, kind, payload=None):
        self.events.append((call_id, kind, payload))

    def update_call(self, call_id, **fields):
        self.updates.append((call_id, fields))


@pytest.fixture
def bus():
    fake = FakeBus()
    observability.set_bus(cast(Any, fake))
    yield fake
    observability.set_bus(None)


def make_meta() -> CallMeta:
    return CallMeta(
        call_id="CA456",
        stream_sid="MZ123",
        from_number="+34612345678",
        connected_at=datetime.now(ZoneInfo("Europe/Madrid")),
    )


def test_completed_turn_keeps_its_call_metadata():
    async def run():
        completed_turns: list[tuple[CallMeta, str]] = []
        turn_received = asyncio.Event()
        meta = make_meta()

        async def capture_turn(call_meta: CallMeta, content: str):
            completed_turns.append((call_meta, content))
            turn_received.set()

        aggregator = create_user_aggregator(meta, capture_turn)
        message = UserTurnStoppedMessage(
            content="Quiero pedir una cita",
            timestamp="2026-09-18T21:00:00Z",
        )
        await aggregator._call_event_handler("on_user_turn_stopped", None, message)
        await asyncio.wait_for(turn_received.wait(), timeout=1)
        return completed_turns

    completed_turns = asyncio.run(run())

    assert len(completed_turns) == 1
    captured_meta, content = completed_turns[0]
    assert content == "Quiero pedir una cita"
    assert captured_meta.call_id == "CA456"
    assert captured_meta.stream_sid == "MZ123"


def test_completed_turn_emits_final_transcript_and_thinking_state(bus):
    async def run():
        meta = make_meta()
        aggregator = create_user_aggregator(meta, lambda *_: asyncio.sleep(0))
        message = UserTurnStoppedMessage(
            content="Quiero pedir una cita",
            timestamp="2026-09-18T21:00:00Z",
        )
        await aggregator._call_event_handler("on_user_turn_stopped", None, message)

    asyncio.run(run())

    assert ("CA456", "stt_final", {"text": "Quiero pedir una cita"}) in bus.events
    assert ("CA456", {"state": "thinking"}) in bus.updates


def test_empty_turn_asks_caller_to_repeat():
    async def run():
        frames = []
        aggregator = create_user_aggregator(
            make_meta(),
            lambda *_: asyncio.sleep(0),
        )

        async def capture_frame(frame, _direction):
            frames.append(frame)

        aggregator.push_frame = capture_frame
        message = UserTurnStoppedMessage(
            content="",
            timestamp="2026-09-18T21:00:00Z",
        )
        await aggregator._call_event_handler("on_user_turn_stopped", None, message)
        return frames

    frames = asyncio.run(run())

    assert [type(frame) for frame in frames] == [TTSRequestedFrame, TTSSpeakFrame]
    assert [frame.text for frame in frames] == [
        EMPTY_TURN_RECOVERY_MESSAGE,
        EMPTY_TURN_RECOVERY_MESSAGE,
    ]


def test_transcript_observer_emits_partial_and_forwards_the_frame(bus):
    class CapturingObserver(TranscriptObserver):
        def __init__(self, meta):
            super().__init__(meta)
            self.forwarded = []

        async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
            self.forwarded.append(frame)

    async def run():
        observer = CapturingObserver(make_meta())
        frame = InterimTranscriptionFrame(
            text="Quería ci", user_id="caller", timestamp="2026-09-18T21:00:00Z"
        )
        await observer.process_frame(frame, FrameDirection.DOWNSTREAM)
        return observer.forwarded, frame

    forwarded, frame = asyncio.run(run())

    assert forwarded == [frame]
    assert bus.events == [("CA456", "stt_partial", {"text": "Quería ci"})]
    assert bus.updates == [("CA456", {"state": "listening"})]


def test_concurrent_calls_initialize_the_shared_database_once(monkeypatch):
    class FakeDatabase:
        def __init__(self):
            self.init_calls = 0

        async def init(self):
            self.init_calls += 1
            await asyncio.sleep(0.01)

    class FakeRepository:
        def __init__(self, _database):
            self.call_ids = []

        async def seed_default_guardrails(self):
            return None

        async def create_call(self, call_id, *_args):
            self.call_ids.append(call_id)

    database = FakeDatabase()
    monkeypatch.setattr(transcription, "CallRepository", FakeRepository)
    monkeypatch.setattr(transcription, "VADProcessor", lambda **_kwargs: object())
    monkeypatch.setattr(transcription, "SileroVADAnalyzer", lambda: object())
    monkeypatch.setattr(transcription, "create_deepgram_stt", lambda: object())
    monkeypatch.setattr(transcription, "DeepgramEOTCoordinator", lambda: object())
    monkeypatch.setattr(transcription, "TranscriptObserver", lambda _meta: object())
    monkeypatch.setattr(
        transcription, "create_user_aggregator", lambda *_args: object()
    )
    monkeypatch.setattr(transcription, "AgentReply", lambda *_args: object())
    monkeypatch.setattr(transcription, "create_tts", lambda: object())
    build_agent = transcription.create_transcription_agent(
        database=cast(Database, database)
    )

    async def run():
        await asyncio.gather(
            *(
                build_agent(
                    CallMeta(
                        call_id=f"CA{index}",
                        stream_sid=f"MZ{index}",
                        from_number=None,
                        connected_at=make_meta().connected_at,
                    )
                )
                for index in range(20)
            )
        )

    asyncio.run(run())

    assert database.init_calls == 1
