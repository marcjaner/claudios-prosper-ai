import asyncio
import time
from threading import Event
from typing import cast

from agent import agent
from agent.llm import (
    LLMClient,
    LLMToolCall,
    StructuredCompletion,
    ToolCompletion,
    Usage,
)
from agent.models import AgentResponse, Tool
from storage import CallRepository


class FakeRepository:
    async def append_event(self, *_args):
        return None

    async def memory_for_call(self, _call_id):
        return ""

    async def record_submission(self, *_args):
        return None


class FakeClinicApi:
    def close(self):
        pass


def test_llm_completion_does_not_block_event_loop(monkeypatch):
    completion_started = Event()

    class SlowLLMClient:
        default_model = "test-model"

        def complete_with_tools(self, *_args, **_kwargs):
            completion_started.set()
            time.sleep(0.1)
            return ToolCompletion(text="Hola", tool_calls=[], usage=Usage())

    monkeypatch.setattr(agent.ClinicApi, "from_environment", lambda: FakeClinicApi())

    async def run():
        responses = asyncio.create_task(
            collect_responses(
                agent.run_agent_turn(
                    "Hola",
                    "CA123",
                    cast(CallRepository, FakeRepository()),
                    cast(LLMClient, SlowLLMClient()),
                )
            )
        )
        while not completion_started.is_set():
            await asyncio.sleep(0)
        await asyncio.sleep(0.01)
        assert not responses.done()
        return await responses

    async def collect_responses(responses):
        return [response async for response in responses]

    responses = asyncio.run(run())

    assert responses == [AgentResponse(immediate_answer="Hola")]


def test_clinic_tool_does_not_block_event_loop(monkeypatch):
    tool_started = Event()

    def slow_tool():
        tool_started.set()
        time.sleep(0.1)
        return {"matches": []}

    class ToolCallingLLMClient:
        default_model = "test-model"

        def complete_with_tools(self, *_args, **_kwargs):
            return ToolCompletion(
                text="Un momento",
                tool_calls=[
                    LLMToolCall(
                        call_id="tool-1",
                        name="search_patients",
                        arguments={},
                    )
                ],
                usage=Usage(),
            )

        def complete_structured(self, *_args, **_kwargs):
            return StructuredCompletion(
                data=AgentResponse(immediate_answer="No encuentro el paciente"),
                usage=Usage(),
            )

    tools = {
        "search_patients": Tool(
            name="search_patients", parameters={"type": "object"}, execute=slow_tool
        )
    }
    monkeypatch.setattr(agent, "load_tools", lambda _functions: tools)
    monkeypatch.setattr(agent.ClinicApi, "from_environment", lambda: FakeClinicApi())

    async def run():
        responses = asyncio.create_task(
            collect_responses(
                agent.run_agent_turn(
                    "Soy Ana",
                    "CA123",
                    cast(CallRepository, FakeRepository()),
                    cast(LLMClient, ToolCallingLLMClient()),
                )
            )
        )
        while not tool_started.is_set():
            await asyncio.sleep(0)
        await asyncio.sleep(0.01)
        assert not responses.done()
        return await responses

    async def collect_responses(responses):
        return [response async for response in responses]

    responses = asyncio.run(run())

    assert [response.immediate_answer for response in responses] == [
        "Un momento",
        "No encuentro el paciente",
    ]
