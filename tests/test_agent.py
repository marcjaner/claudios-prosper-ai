import asyncio
import time
from threading import Event
from typing import cast

from agent import agent
from agent.graph import parse_graph
from agent.llm import (
    LLMClient,
    LLMToolCall,
    StructuredCompletion,
    ToolCompletion,
    Usage,
)
from agent.models import AgentResponse, Tool
from agent.stage_runtime import CallGraph
from storage import CallRepository


class FakeRepository:
    def __init__(self):
        self.events = []
        self.submissions = []
        self.workflow = None

    async def append_event(self, *_args):
        self.events.append(_args)

    async def memory_for_call(self, _call_id):
        return ""

    async def workflow_for_call(self, _call_id):
        return {}

    async def record_submission(self, *_args):
        self.submissions.append(_args)

    async def save_workflow(self, _call_id, workflow):
        self.workflow = workflow

    async def seed_default_guardrails(self):
        pass

    async def list_guardrails(self):
        return []


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
        calls = 0

        def complete_with_tools(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls > 1:
                return ToolCompletion(
                    text="No encuentro el paciente", tool_calls=[], usage=Usage()
                )
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

    tools = {
        "search_patients": Tool(
            name="search_patients", parameters={}, execute=slow_tool
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


def test_context_survives_agent_turns_and_records_only_confirmed_payload(monkeypatch):
    class SchedulingApi:
        def __init__(self):
            self.posts = []

        def close(self):
            pass

        def search_patients(self, **_kwargs):
            return {"matches": [{"patient_id": "P00042"}]}

        def search_availability(self, **_kwargs):
            return {
                "slots": [
                    {
                        "provider_id": "PR05",
                        "location_id": "sur",
                        "appointment_type_id": "review",
                        "specialty_id": "dermatology",
                        "start_time": "2026-09-24T16:30:00+02:00",
                        "payable_with": ["sanitas"],
                    }
                ]
            }

        def book(self, request):
            self.posts.append(request.model_dump(mode="json"))
            return {"record": {"actions": [{"action": "BOOK"}]}}

    class ScriptedClient:
        default_model = "test-model"

        def __init__(self):
            self.completions = iter(
                [
                    ToolCompletion(
                        text="Un momento",
                        tool_calls=[
                            LLMToolCall(
                                call_id="1",
                                name="search_patients",
                                arguments={"national_id": "12345678Z"},
                            )
                        ],
                        usage=Usage(),
                    ),
                    ToolCompletion(
                        text="Busco una cita",
                        tool_calls=[
                            LLMToolCall(
                                call_id="2",
                                name="search_availability",
                                arguments={
                                    "date_from": "2026-09-24",
                                    "date_to": "2026-09-24",
                                    "patient_id": "P00042",
                                    "specialty_id": "dermatology",
                                },
                            )
                        ],
                        usage=Usage(),
                    ),
                    ToolCompletion(
                        text="Le ofrezco esta cita",
                        tool_calls=[
                            LLMToolCall(
                                call_id="3",
                                name="prepare_booking",
                                arguments={
                                    "slot_id": "slot_1",
                                    "policy_id": "sanitas",
                                },
                            )
                        ],
                        usage=Usage(),
                    ),
                    ToolCompletion(
                        text="¿Confirma esta cita?",
                        tool_calls=[],
                        usage=Usage(),
                    ),
                    ToolCompletion(
                        text="Confirmo la cita",
                        tool_calls=[
                            LLMToolCall(
                                call_id="4",
                                name="confirm_action",
                                arguments={"proposal_id": "prop_1"},
                            )
                        ],
                        usage=Usage(),
                    ),
                    ToolCompletion(
                        text="La cita ha quedado confirmada",
                        tool_calls=[],
                        usage=Usage(),
                    ),
                ]
            )

        def complete_with_tools(self, *_args, **_kwargs):
            return next(self.completions)

        def complete_structured(self, *_args, **_kwargs):
            return StructuredCompletion(
                data=AgentResponse(immediate_answer="¿Confirma estos datos?"),
                usage=Usage(),
            )

    api = SchedulingApi()
    repository = FakeRepository()
    graph = parse_graph(
        {
            "entry": "schedule",
            "nodes": [
                {
                    "id": "schedule",
                    "tools": [
                        "search_patients",
                        "search_availability",
                        "prepare_booking",
                        "confirm_action",
                    ],
                }
            ],
        }
    )
    state = CallGraph.start(graph, call_id="CA123")
    context = state.context
    client = ScriptedClient()
    monkeypatch.setattr(agent.ClinicApi, "from_environment", lambda: api)

    async def run():
        await collect_responses(
            agent.run_agent_turn(
                "Quiero una cita",
                "CA123",
                cast(CallRepository, repository),
                cast(LLMClient, client),
                state=state,
            )
        )
        assert state.facts["proposal_id"] == "prop_1"
        assert repository.workflow is not None
        assert repository.workflow["stage"] == "schedule"
        assert (
            repository.workflow["request_context"]["proposals"][0]["status"]
            == "prepared"
        )
        await collect_responses(
            agent.run_agent_turn(
                "Sí, confirmo",
                "CA123",
                cast(CallRepository, repository),
                cast(LLMClient, client),
                state=state,
            )
        )

    async def collect_responses(responses):
        return [response async for response in responses]

    asyncio.run(run())

    expected_payload = {
        "call_id": "CA123",
        "patient_id": "P00042",
        "provider_id": "PR05",
        "location_id": "sur",
        "appointment_type_id": "review",
        "slot": "2026-09-24T16:30:00+02:00",
        "policy_id": "sanitas",
    }
    assert context.turn_number == 2
    assert "proposal_id" not in state.facts
    assert api.posts == [expected_payload]
    assert repository.submissions == [
        (
            "CA123",
            "BOOK",
            expected_payload,
            200,
            {"record": {"actions": [{"action": "BOOK"}]}},
        )
    ]
