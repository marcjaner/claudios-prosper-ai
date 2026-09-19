import asyncio
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from pipecat.frames.frames import LLMContextFrame, TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

import observability
from agent.clinic_api import ClinicApi
from agent.reply import AGENT_ERROR_REPLY, AgentReply
from twilio import CallMeta


class FakeClinicApi:
    def __init__(self):
        self.is_closed = False

    def close(self):
        self.is_closed = True


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


class CapturingAgentReply(AgentReply):
    def __init__(self, meta, clinic_api, runner):
        super().__init__(meta, cast(ClinicApi, clinic_api), runner=runner)
        self.emitted = []

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.emitted.append(frame)


def make_meta() -> CallMeta:
    return CallMeta(
        call_id="CA456",
        stream_sid="MZ123",
        from_number="+34612345678",
        connected_at=datetime(2026, 9, 19, 10, tzinfo=ZoneInfo("Europe/Madrid")),
    )


def make_context() -> LLMContext:
    return LLMContext(
        [
            {"role": "user", "content": "Quiero una cita."},
            {"role": "assistant", "content": "¿De qué especialidad?"},
            {"role": "user", "content": "Dermatología."},
        ]
    )


def test_agent_reply_runs_real_agent_with_call_context(bus):
    async def run():
        calls = []
        clinic_api = FakeClinicApi()
        meta = make_meta()

        def runner(prompt, client, *, clinic_api, call_id):
            calls.append((prompt, client, clinic_api, call_id))
            yield "Claro. ¿Qué día le viene bien?"

        processor = CapturingAgentReply(meta, clinic_api, runner)
        await processor.process_frame(
            LLMContextFrame(make_context()), FrameDirection.DOWNSTREAM
        )
        await processor.cleanup()
        return calls, clinic_api, processor.emitted

    calls, clinic_api, emitted = asyncio.run(run())

    prompt, client, captured_api, call_id = calls[0]
    assert "2026-09-19" in prompt
    assert "Caller ID hint: +34612345678" in prompt
    assert "assistant: ¿De qué especialidad?" in prompt
    assert "user: Dermatología." in prompt
    assert client is None
    assert captured_api is clinic_api
    assert call_id == "CA456"
    assert isinstance(emitted[0], TTSSpeakFrame)
    assert emitted[0].text == "Claro. ¿Qué día le viene bien?"
    assert clinic_api.is_closed is True

    kinds = [kind for _, kind, _ in bus.events]
    assert ("CA456", "tts", {"text": "Claro. ¿Qué día le viene bien?"}) in bus.events
    assert "error" not in kinds
    states = [fields.get("state") for _, fields in bus.updates]
    assert states == ["thinking", "speaking"]


def test_agent_reply_falls_back_without_ending_the_call(bus):
    async def run():
        clinic_api = FakeClinicApi()

        def runner(prompt, client, *, clinic_api, call_id):
            raise RuntimeError("LLM exploded")
            yield

        processor = CapturingAgentReply(make_meta(), clinic_api, runner)
        await processor.process_frame(
            LLMContextFrame(make_context()), FrameDirection.DOWNSTREAM
        )
        return processor.emitted

    emitted = asyncio.run(run())

    assert isinstance(emitted[0], TTSSpeakFrame)
    assert emitted[0].text == AGENT_ERROR_REPLY
    assert ("CA456", "error", {"message": "LLM exploded"}) in bus.events
    assert ("CA456", "tts", {"text": AGENT_ERROR_REPLY}) in bus.events
