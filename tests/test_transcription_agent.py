import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from pipecat.frames.frames import TTSSpeakFrame
from pipecat.processors.aggregators.llm_response_universal import (
    UserTurnStoppedMessage,
)

from agent.transcription import EMPTY_TURN_RECOVERY_MESSAGE, create_user_aggregator
from observability.frames import TTSRequestedFrame
from twilio import CallMeta


def test_completed_turn_keeps_its_call_metadata():
    async def run():
        completed_turns: list[tuple[CallMeta, str]] = []
        turn_received = asyncio.Event()
        meta = CallMeta(
            call_id="CA456",
            stream_sid="MZ123",
            from_number="+34612345678",
            connected_at=datetime.now(ZoneInfo("Europe/Madrid")),
        )

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


def test_empty_turn_asks_caller_to_repeat():
    async def run():
        frames = []
        meta = CallMeta(
            call_id="CA456",
            stream_sid="MZ123",
            from_number="+34612345678",
            connected_at=datetime.now(ZoneInfo("Europe/Madrid")),
        )

        async def capture_turn(_meta: CallMeta, _content: str):
            raise AssertionError("empty turns must not reach the agent")

        aggregator = create_user_aggregator(meta, capture_turn)

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
