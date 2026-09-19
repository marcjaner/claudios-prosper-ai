import asyncio
from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo

from pipecat.frames.frames import LLMContextFrame, TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from agent.clinic_api import ClinicApi
from agent.reply import AgentReply
from twilio import CallMeta


class FakeClinicApi:
    def __init__(self):
        self.is_closed = False

    def close(self):
        self.is_closed = True


class CapturingAgentReply(AgentReply):
    def __init__(self, meta, clinic_api, runner):
        super().__init__(meta, cast(ClinicApi, clinic_api), runner=runner)
        self.emitted = []

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.emitted.append(frame)


def test_agent_reply_runs_real_agent_with_call_context():
    async def run():
        calls = []
        clinic_api = FakeClinicApi()
        meta = CallMeta(
            call_id="CA456",
            stream_sid="MZ123",
            from_number="+34612345678",
            connected_at=datetime(2026, 9, 19, 10, tzinfo=ZoneInfo("Europe/Madrid")),
        )

        def runner(prompt, client, *, clinic_api, call_id):
            calls.append((prompt, client, clinic_api, call_id))
            yield "Of course. What day works for you?"

        processor = CapturingAgentReply(meta, clinic_api, runner)
        context = LLMContext(
            [
                {"role": "user", "content": "I need an appointment."},
                {"role": "assistant", "content": "Which specialty?"},
                {"role": "user", "content": "Dermatology."},
            ]
        )
        await processor.process_frame(
            LLMContextFrame(context), FrameDirection.DOWNSTREAM
        )
        await processor.cleanup()
        return calls, clinic_api, processor.emitted

    calls, clinic_api, emitted = asyncio.run(run())

    prompt, client, captured_api, call_id = calls[0]
    assert "Caller ID hint: +34612345678" in prompt
    assert "assistant: Which specialty?" in prompt
    assert "user: Dermatology." in prompt
    assert client is None
    assert captured_api is clinic_api
    assert call_id == "CA456"
    assert isinstance(emitted[0], TTSSpeakFrame)
    assert emitted[0].text == "Of course. What day works for you?"
    assert clinic_api.is_closed is True
