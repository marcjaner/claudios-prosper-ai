import asyncio
from typing import cast

import pytest
from pipecat.frames.frames import LLMContextFrame, TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

import agent.reply as reply_module
from agent.agent import run_agent_turn
from agent.clinic_api import ClinicApi
from agent.language import DEFAULT_LANGUAGE, phrases
from agent.llm import (
    LLMClient,
    LLMToolCall,
    StructuredCompletion,
    ToolCompletion,
    Usage,
)
from agent.models import AgentResponse, ToolCall
from agent.reply import AgentReply
from observability.frames import (
    LLMRequestFailedFrame,
    LLMRequestStartedFrame,
    LLMResponseFinishedFrame,
    ToolCallFinishedFrame,
    ToolCallStartedFrame,
    TTSRequestedFrame,
)
from storage import CallRepository
from twilio.recording import CallTimelineObserver


class FakeRepository:
    def __init__(self):
        self.events = []
        self.submissions = []
        self.workflow = {}

    async def append_event(self, call_id, event_type, payload):
        self.events.append((call_id, event_type, payload))

    async def memory_for_call(self, call_id):
        return ""

    async def workflow_for_call(self, call_id):
        return {}

    async def save_workflow(self, call_id, state):
        self.workflow = state

    async def record_submission(self, *arguments):
        self.submissions.append(arguments)

    async def seed_default_guardrails(self):
        pass

    async def list_guardrails(self):
        return []


class FakeClinicApi:
    def __init__(self, error: Exception | None = None):
        self._error = error
        self.is_closed = False

    def search_patients(self, **arguments):
        if self._error:
            raise self._error
        return {"patients": [], "query": arguments}

    def close(self):
        self.is_closed = True


class FakeLLMClient:
    default_model = "test-model"

    def __init__(self, responses: list[AgentResponse | Exception]):
        self._responses = iter(responses)

    @staticmethod
    def _usage():
        return Usage(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            reasoning_tokens=2,
            cached_tokens=3,
        )

    def complete_with_tools(self, *args, **kwargs):
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return ToolCompletion(
            text=response.immediate_answer,
            tool_calls=[
                LLMToolCall(
                    call_id=f"call-{index}",
                    name=call.name,
                    arguments=call.arguments,
                )
                for index, call in enumerate(response.tool_calls)
            ],
            usage=self._usage(),
        )

    def complete_structured(self, *args, **kwargs):
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return StructuredCompletion(
            data=response,
            usage=self._usage(),
        )


class CapturingAgentReply(AgentReply):
    def __init__(self, repository: FakeRepository):
        super().__init__("CA456", cast(CallRepository, repository))
        self.emitted = []

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.emitted.append(frame)


def run_observed_turn(
    monkeypatch,
    responses: list[AgentResponse | Exception],
    clinic_error: Exception | None = None,
):
    clinic_api = FakeClinicApi(clinic_error)
    monkeypatch.setattr(
        ClinicApi, "from_environment", classmethod(lambda cls: clinic_api)
    )

    async def run():
        frames = []
        replies = []

        async def emit(frame):
            frames.append(frame)

        async for response in run_agent_turn(
            "Hello",
            "CA456",
            cast(CallRepository, FakeRepository()),
            cast(LLMClient, FakeLLMClient(responses)),
            event_sink=emit,
        ):
            replies.append(response)
        return frames, replies

    frames, replies = asyncio.run(run())
    assert clinic_api.is_closed is True
    return frames, replies


def test_successful_turn_emits_llm_events(monkeypatch):
    frames, replies = run_observed_turn(
        monkeypatch, [AgentResponse(immediate_answer="How can I help?")]
    )

    assert [type(frame) for frame in frames] == [
        LLMRequestStartedFrame,
        LLMResponseFinishedFrame,
    ]
    assert frames[0].request_id == frames[1].request_id
    assert frames[0].model == frames[1].model == "test-model"
    assert frames[1].duration_ms > 0
    assert frames[1].total_tokens == 15
    assert [response.immediate_answer for response in replies] == ["How can I help?"]


def test_tool_turn_observes_initial_and_follow_up_llm_requests(monkeypatch):
    frames, replies = run_observed_turn(
        monkeypatch,
        [
            AgentResponse(
                immediate_answer="",
                tool_calls=[
                    ToolCall(name="search_patients", arguments={"name": "Ana"})
                ],
            ),
            AgentResponse(immediate_answer="I found your record."),
        ],
    )

    assert [type(frame) for frame in frames] == [
        LLMRequestStartedFrame,
        LLMResponseFinishedFrame,
        ToolCallStartedFrame,
        ToolCallFinishedFrame,
        LLMRequestStartedFrame,
        LLMResponseFinishedFrame,
    ]
    tool_started = cast(ToolCallStartedFrame, frames[2])
    tool_finished = cast(ToolCallFinishedFrame, frames[3])
    assert tool_started.tool_call_id == tool_finished.tool_call_id
    assert tool_started.arguments == {"name": "Ana"}
    assert tool_finished.duration_ms > 0
    assert tool_finished.status == "success"
    assert tool_finished.result_summary == {
        "type": "object",
        "keys": ["patients", "query"],
        "item_count": 2,
    }
    assert frames[0].request_id == frames[1].request_id
    assert frames[4].request_id == frames[5].request_id
    assert frames[0].request_id != frames[4].request_id
    assert [response.immediate_answer for response in replies] == [
        phrases(DEFAULT_LANGUAGE).acknowledgement,
        "I found your record.",
    ]


def test_agent_reply_requests_tts_for_each_shipped_agent_response(monkeypatch):
    async def fake_run_agent_turn(*args, event_sink, **kwargs):
        await event_sink(LLMRequestStartedFrame("request-1", "test-model"))
        await event_sink(
            LLMResponseFinishedFrame("request-1", "test-model", 12.5, 10, 5, 15, 2, 3)
        )
        yield AgentResponse(immediate_answer="Let me check.")
        await event_sink(ToolCallStartedFrame("search_patients", "tool-1", {}))
        await event_sink(
            ToolCallFinishedFrame("search_patients", "tool-1", 8.2, "success")
        )
        await event_sink(LLMRequestStartedFrame("request-2", "test-model"))
        await event_sink(
            LLMResponseFinishedFrame("request-2", "test-model", 9.5, 12, 4, 16, 0, 2)
        )
        yield AgentResponse(immediate_answer="I found your record.")

    monkeypatch.setattr(reply_module, "run_agent_turn", fake_run_agent_turn)

    async def run():
        processor = CapturingAgentReply(FakeRepository())
        await processor.process_frame(
            LLMContextFrame(LLMContext([{"role": "user", "content": "Hello"}])),
            FrameDirection.DOWNSTREAM,
        )
        return processor.emitted

    emitted = asyncio.run(run())
    assert [type(frame) for frame in emitted] == [
        LLMRequestStartedFrame,
        LLMResponseFinishedFrame,
        TTSRequestedFrame,
        TTSSpeakFrame,
        ToolCallStartedFrame,
        ToolCallFinishedFrame,
        LLMRequestStartedFrame,
        LLMResponseFinishedFrame,
        TTSRequestedFrame,
        TTSSpeakFrame,
    ]
    assert emitted[2].text == emitted[3].text == "Let me check."
    assert emitted[8].text == emitted[9].text == "I found your record."


def test_agent_reply_relays_events_before_turn_finishes(monkeypatch):
    async def run():
        release_turn = asyncio.Event()
        started = LLMRequestStartedFrame("request-1", "test-model")

        async def fake_run_agent_turn(*args, event_sink, **kwargs):
            await event_sink(started)
            await release_turn.wait()
            yield AgentResponse(immediate_answer="Done")

        monkeypatch.setattr(reply_module, "run_agent_turn", fake_run_agent_turn)
        processor = CapturingAgentReply(FakeRepository())
        task = asyncio.create_task(
            processor.process_frame(
                LLMContextFrame(LLMContext([{"role": "user", "content": "Hello"}])),
                FrameDirection.DOWNSTREAM,
            )
        )
        try:
            await asyncio.sleep(0)
            assert processor.emitted == [started]
            assert not task.done()
        finally:
            release_turn.set()
            await task

    asyncio.run(run())


def test_llm_failure_emits_safe_failure_event(monkeypatch):
    clinic_api = FakeClinicApi()
    monkeypatch.setattr(
        ClinicApi, "from_environment", classmethod(lambda cls: clinic_api)
    )

    async def run():
        frames = []

        async def emit(frame):
            frames.append(frame)

        with pytest.raises(RuntimeError, match="secret provider details"):
            async for _ in run_agent_turn(
                "Hello",
                "CA456",
                cast(CallRepository, FakeRepository()),
                cast(
                    LLMClient, FakeLLMClient([RuntimeError("secret provider details")])
                ),
                event_sink=emit,
            ):
                pass
        return frames

    frames = asyncio.run(run())
    assert [type(frame) for frame in frames] == [
        LLMRequestStartedFrame,
        LLMRequestFailedFrame,
    ]
    assert frames[0].request_id == frames[1].request_id
    assert frames[1].duration_ms > 0
    assert frames[1].error_type == "RuntimeError"
    assert frames[1].error_message == "LLM request failed"
    assert "secret" not in frames[1].error_message
    assert clinic_api.is_closed is True


def test_tool_failure_is_reported_safely_and_the_turn_still_answers(monkeypatch):
    """A clinic outage becomes feedback the agent can voice, not a silent dead turn."""
    clinic_api = FakeClinicApi(RuntimeError("secret clinic details"))
    monkeypatch.setattr(
        ClinicApi, "from_environment", classmethod(lambda cls: clinic_api)
    )

    async def run():
        frames = []
        replies = []

        async def emit(frame):
            frames.append(frame)

        async for response in run_agent_turn(
            "Hello",
            "CA456",
            cast(CallRepository, FakeRepository()),
            cast(
                LLMClient,
                FakeLLMClient(
                    [
                        AgentResponse(
                            immediate_answer="Let me check.",
                            tool_calls=[
                                ToolCall(
                                    name="search_patients",
                                    arguments={"name": "Ana"},
                                )
                            ],
                        ),
                        AgentResponse(
                            immediate_answer="Sorry, I could not look that up."
                        ),
                    ]
                ),
            ),
            event_sink=emit,
        ):
            replies.append(response)
        return frames, replies

    frames, replies = asyncio.run(run())
    finished = next(
        frame for frame in frames if isinstance(frame, ToolCallFinishedFrame)
    )
    assert finished.duration_ms > 0
    assert finished.status == "error"
    assert finished.error_type == "RuntimeError"
    assert finished.error_message == "Tool execution failed"
    assert "secret" not in finished.error_message
    assert [response.immediate_answer for response in replies] == [
        "Let me check.",
        "Sorry, I could not look that up.",
    ]
    assert clinic_api.is_closed is True


def test_tool_arguments_exclude_credentials_headers_and_hidden_call_id(monkeypatch):
    frames, _ = run_observed_turn(
        monkeypatch,
        [
            AgentResponse(
                immediate_answer="Let me check.",
                tool_calls=[
                    ToolCall(
                        name="search_patients",
                        arguments={
                            "name": "Ana",
                            "api_key": "secret",
                            "call_id": "hidden",
                            "metadata": {
                                "request_headers": {"Authorization": "Bearer secret"},
                                "patient_id": "P123",
                            },
                        },
                    )
                ],
            ),
            AgentResponse(immediate_answer="Nobody by that name."),
        ],
    )

    started = next(frame for frame in frames if isinstance(frame, ToolCallStartedFrame))
    assert started.arguments == {
        "name": "Ana",
        "metadata": {"patient_id": "P123"},
    }


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        (
            LLMRequestStartedFrame("request-1", "model-1"),
            {
                "event": "llm_request_started",
                "request_id": "request-1",
                "model": "model-1",
            },
        ),
        (
            LLMResponseFinishedFrame("request-1", "model-1", 12.5, 10, 5, 15, 2, 3),
            {
                "event": "llm_response_finished",
                "request_id": "request-1",
                "model": "model-1",
                "duration_ms": 12.5,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "reasoning_tokens": 2,
                "cached_tokens": 3,
            },
        ),
        (
            LLMRequestFailedFrame(
                "request-1", "model-1", 12.5, "RuntimeError", "LLM request failed"
            ),
            {
                "event": "llm_request_failed",
                "request_id": "request-1",
                "model": "model-1",
                "duration_ms": 12.5,
                "error_type": "RuntimeError",
                "error_message": "LLM request failed",
            },
        ),
        (
            ToolCallStartedFrame("search_patients", "tool-1", {"name": "Ana"}),
            {
                "event": "tool_call_started",
                "tool": "search_patients",
                "tool_call_id": "tool-1",
                "arguments": {"name": "Ana"},
            },
        ),
        (
            ToolCallFinishedFrame(
                "search_patients",
                "tool-1",
                8.2,
                "success",
                {"type": "object", "item_count": 1},
            ),
            {
                "event": "tool_call_finished",
                "tool": "search_patients",
                "tool_call_id": "tool-1",
                "duration_ms": 8.2,
                "status": "success",
                "result_summary": {"type": "object", "item_count": 1},
                "error_type": None,
                "error_message": None,
            },
        ),
        (
            TTSRequestedFrame("Hello"),
            {"event": "tts_requested", "text": "Hello"},
        ),
    ],
)
def test_call_timeline_serializes_latency_frames(frame, expected):
    assert (
        CallTimelineObserver._event_for_frame(frame, FrameDirection.DOWNSTREAM)
        == expected
    )
