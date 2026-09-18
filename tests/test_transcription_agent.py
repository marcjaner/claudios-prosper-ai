import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from pipecat.processors.aggregators.llm_response_universal import (
    UserTurnStoppedMessage,
)

from agent.transcription import create_user_aggregator
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
