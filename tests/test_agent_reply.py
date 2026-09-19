import asyncio
from typing import Any, cast

import pytest
from pipecat.frames.frames import LLMContextFrame, TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

import agent.reply
import observability
from agent.models import AgentResponse
from agent.reply import AGENT_ERROR_REPLY, AgentReply
from observability.frames import TTSRequestedFrame


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
    def __init__(self, call_id, repository):
        super().__init__(call_id, repository)
        self.emitted = []

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.emitted.append(frame)


def make_context() -> LLMContext:
    return LLMContext(
        [
            {"role": "user", "content": "Quiero una cita."},
            {"role": "assistant", "content": "¿De qué especialidad?"},
            {"role": "user", "content": "Dermatología."},
        ]
    )


def fake_runner(calls, responses=None, error=None):
    async def runner(prompt, call_id, repository, **kwargs):
        calls.append((prompt, call_id, repository, kwargs))
        if error is not None:
            raise error
        for response in responses or []:
            yield response

    return runner


def test_agent_reply_speaks_every_turn_response(monkeypatch, bus):
    calls = []
    repository = object()
    responses = [
        AgentResponse(immediate_answer="Un momento."),
        AgentResponse(immediate_answer="Reservada para el jueves."),
    ]
    monkeypatch.setattr(agent.reply, "run_agent_turn", fake_runner(calls, responses))
    processor = CapturingAgentReply("CA456", repository)

    asyncio.run(
        processor.process_frame(
            LLMContextFrame(make_context()), FrameDirection.DOWNSTREAM
        )
    )

    prompt, call_id, captured_repo, _kwargs = calls[0]
    assert call_id == "CA456"
    assert captured_repo is repository
    assert prompt == "Dermatología."

    requested = [f for f in processor.emitted if isinstance(f, TTSRequestedFrame)]
    spoken = [f for f in processor.emitted if isinstance(f, TTSSpeakFrame)]
    assert [f.text for f in requested] == [
        "Un momento.",
        "Reservada para el jueves.",
    ]
    assert [f.text for f in spoken] == [
        "Un momento.",
        "Reservada para el jueves.",
    ]

    tts = [e for e in bus.events if e[1] == "tts"]
    assert tts == [
        ("CA456", "tts", {"text": "Un momento."}),
        ("CA456", "tts", {"text": "Reservada para el jueves."}),
    ]
    states = [fields.get("state") for _, fields in bus.updates]
    assert states == ["thinking", "speaking", "speaking"]


def test_agent_reply_falls_back_without_ending_the_call(monkeypatch, bus):
    calls = []
    monkeypatch.setattr(
        agent.reply,
        "run_agent_turn",
        fake_runner(calls, error=RuntimeError("LLM exploded")),
    )
    processor = CapturingAgentReply("CA456", object())

    asyncio.run(
        processor.process_frame(
            LLMContextFrame(make_context()), FrameDirection.DOWNSTREAM
        )
    )

    requested = [f for f in processor.emitted if isinstance(f, TTSRequestedFrame)]
    spoken = [f for f in processor.emitted if isinstance(f, TTSSpeakFrame)]
    assert [f.text for f in requested] == [AGENT_ERROR_REPLY]
    assert [f.text for f in spoken] == [AGENT_ERROR_REPLY]
    assert ("CA456", "error", {"message": "LLM exploded"}) in bus.events
    assert ("CA456", "tts", {"text": AGENT_ERROR_REPLY}) in bus.events


def test_speculative_context_is_forwarded_without_running(monkeypatch, bus):
    calls = []
    monkeypatch.setattr(agent.reply, "run_agent_turn", fake_runner(calls))
    processor = CapturingAgentReply("CA456", object())
    frame = LLMContextFrame(make_context())
    frame.speculation = True

    asyncio.run(processor.process_frame(frame, FrameDirection.DOWNSTREAM))

    assert calls == []
    assert processor.emitted == [frame]
    assert not bus.events
    assert not bus.updates
