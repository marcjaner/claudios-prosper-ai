import asyncio
from typing import Any, cast

import pytest

import observability
from agent.agent import run_agent, run_agent_turn
from agent.clinic_api import ClinicApi, ProsperApiError
from agent.llm import (
    LLMToolCall,
    StructuredCompletion,
    ToolCompletion,
    Usage,
)
from agent.models import AgentResponse, ToolCall, ToolResult
from agent.stage_runtime import CallGraph


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


class FakeClient:
    def __init__(self, response: AgentResponse):
        self._response = response

    def complete_with_tools(self, prompt, tools, **kwargs):
        response = self._response
        return ToolCompletion(
            text=response.immediate_answer,
            tool_calls=[
                LLMToolCall(call_id=str(i), name=c.name, arguments=c.arguments)
                for i, c in enumerate(response.tool_calls)
            ],
            usage=Usage(),
        )


class FakeClinicApi:

    def __init__(self, results: dict | None = None, fail_with: Exception | None = None):
        self._results = results or {}
        self._fail_with = fail_with

    def _respond(self, name):
        if self._fail_with:
            raise self._fail_with
        return self._results.get(name, {"ok": True})

    def search_patients(self, **kwargs):
        return self._respond("search_patients")

    def book(self, request):
        return self._respond("book_appointment")

    def no_action(self, request):
        return self._respond("submit_no_action")

    def get_clinic(self):
        return self._respond("get_clinic_catalogue")

    def close(self):
        pass


def run(response: AgentResponse, api: FakeClinicApi):
    return list(
        run_agent(
            "hola",
            cast(Any, FakeClient(response)),
            clinic_api=cast(ClinicApi, api),
            call_id="CA456",
        )
    )


def test_tool_call_and_result_share_a_tool_call_id(bus):
    events = run(
        AgentResponse(
            immediate_answer="Un momento.",
            tool_calls=[
                ToolCall(
                    name="book_appointment",
                    arguments={
                        "patient_id": "P00042",
                        "provider_id": "PR05",
                        "location_id": "sur",
                        "appointment_type_id": "review",
                        "slot": "2026-09-24T16:30:00+02:00",
                        "policy_id": "sanitas",
                    },
                )
            ],
        ),
        FakeClinicApi(results={"book_appointment": {"record": {"id": "A1"}}}),
    )

    assert isinstance(events[0], str)
    result = events[1]
    assert isinstance(result, ToolResult)
    assert result.output == {"record": {"id": "A1"}}

    tool_call = next(e for e in bus.events if e[1] == "tool_call")
    tool_result = next(e for e in bus.events if e[1] == "tool_result")
    assert tool_call[2]["name"] == "book_appointment"
    assert tool_result[2]["tool_call_id"] == tool_call[2]["tool_call_id"]
    assert tool_result[2]["status"] == 200
    assert tool_result[2]["response"] == {"record": {"id": "A1"}}


def test_successful_submit_updates_the_call_record(bus):
    run(
        AgentResponse(
            immediate_answer="Reservada.",
            tool_calls=[
                ToolCall(
                    name="book_appointment",
                    arguments={
                        "patient_id": "P00042",
                        "provider_id": "PR05",
                        "location_id": "sur",
                        "appointment_type_id": "review",
                        "slot": "2026-09-24T16:30:00+02:00",
                        "policy_id": "sanitas",
                    },
                )
            ],
        ),
        FakeClinicApi(),
    )

    submit = next(e for e in bus.events if e[1] == "submit")
    assert submit[2]["route"] == "/api/v1/submit/book"
    assert submit[2]["status"] == 200
    assert submit[2]["request"]["patient_id"] == "P00042"
    update = next(u for u in bus.updates if "outcome" in u[1])
    assert update[1]["outcome"] == "BOOK"
    assert update[1]["patient_id"] == "P00042"
    assert update[1]["insurer"] == "sanitas"


def test_no_action_submit_records_its_reason(bus):
    run(
        AgentResponse(
            immediate_answer="No hay huecos.",
            tool_calls=[
                ToolCall(name="submit_no_action", arguments={"reason": "no_availability"})
            ],
        ),
        FakeClinicApi(),
    )

    update = next(u for u in bus.updates if "outcome" in u[1])
    assert update[1]["outcome"] == "NO_ACTION"
    assert update[1]["reason"] == "no_availability"


def test_single_patient_match_identifies_the_caller(bus):
    run(
        AgentResponse(
            immediate_answer="Ya la tengo.",
            tool_calls=[ToolCall(name="search_patients", arguments={"name": "Ana"})],
        ),
        FakeClinicApi(
            results={
                "search_patients": {
                    "matches": [
                        {
                            "patient_id": "P00042",
                            "given_name": "Ana",
                            "first_surname": "García",
                            "second_surname": "López",
                            "insurer": "sanitas",
                        }
                    ]
                }
            }
        ),
    )

    update = next(u for u in bus.updates if "patient_id" in u[1])
    assert update[1]["patient_id"] == "P00042"
    assert update[1]["insurer"] == "sanitas"
    assert update[1]["patient_name"] == "Ana García López"


def test_prosper_error_is_a_tool_result_not_an_outcome(bus):
    events = run(
        AgentResponse(
            immediate_answer="Un momento.",
            tool_calls=[
                ToolCall(
                    name="book_appointment",
                    arguments={
                        "patient_id": "P00042",
                        "provider_id": "PR05",
                        "location_id": "sur",
                        "appointment_type_id": "review",
                        "slot": "2026-09-24T16:30:00+02:00",
                        "policy_id": "sanitas",
                    },
                )
            ],
        ),
        FakeClinicApi(fail_with=ProsperApiError(422, {"detail": "slot taken"})),
    )

    result = events[1]
    assert isinstance(result, ToolResult)
    assert "422" in result.output

    tool_result = next(e for e in bus.events if e[1] == "tool_result")
    assert tool_result[2]["status"] == 422
    assert not any(e[1] == "submit" for e in bus.events)
    assert not any("outcome" in fields for _, fields in bus.updates)


def test_run_agent_without_call_id_emits_nothing():
    events = list(
        run_agent("hola", cast(Any, FakeClient(AgentResponse(immediate_answer="Hola."))))
    )
    assert events == ["Hola."]


BOOK_ARGS = {
    "patient_id": "P00042",
    "provider_id": "PR05",
    "location_id": "sur",
    "appointment_type_id": "review",
    "slot": "2026-09-24T16:30:00+02:00",
    "policy_id": "sanitas",
}


class FakeRepository:
    def __init__(self, state=None):
        self.events = []
        self.submissions = []
        self.workflow = dict(state or {})

    async def seed_default_guardrails(self):
        return None

    async def list_guardrails(self):
        return []

    async def append_event(self, call_id, event_type, payload):
        self.events.append((call_id, event_type, payload))

    async def memory_for_call(self, call_id):
        return ""

    async def workflow_for_call(self, call_id):
        return dict(self.workflow)

    async def save_workflow(self, call_id, state):
        self.workflow = dict(state)

    async def record_submission(
        self, call_id, action, request, response_status, response
    ):
        self.submissions.append((call_id, action, request, response_status, response))


class FakeTurnClient:
    default_model = "fake"

    def __init__(self, response: AgentResponse):
        self._response = response

    def complete_with_tools(self, prompt, tools, **kwargs):
        return ToolCompletion(
            text=self._response.immediate_answer,
            tool_calls=[
                LLMToolCall(call_id=str(i), name=c.name, arguments=c.arguments)
                for i, c in enumerate(self._response.tool_calls)
            ],
            usage=Usage(),
        )

    def complete_structured(self, prompt, schema, **kwargs):
        return StructuredCompletion(
            data=AgentResponse(immediate_answer="Hecho."), usage=Usage()
        )


def run_turn(monkeypatch, response, api, *, state=None, stage=None):
    monkeypatch.setattr(
        ClinicApi, "from_environment", classmethod(lambda cls: api)
    )
    repository = FakeRepository()
    client = cast(Any, FakeTurnClient(response))
    graph_state = CallGraph.start()
    graph_state.facts.update(state or {})
    if stage:
        graph_state.stage_id = stage

    async def collect():
        return [
            item
            async for item in run_agent_turn(
                "hola", "CA456", cast(Any, repository), client, state=graph_state
            )
        ]

    return asyncio.run(collect()), repository, graph_state


BOOKING_STAGE = {"stage": "reservar", "state": {"patient_id": "P00042", "slot_elegido": BOOK_ARGS["slot"]}}


def test_turn_book_emits_tool_events_outcome_and_one_submission(
    monkeypatch, bus
):
    responses, repository, _graph = run_turn(
        monkeypatch,
        AgentResponse(
            immediate_answer="Un momento.",
            tool_calls=[ToolCall(name="book_appointment", arguments=BOOK_ARGS)],
        ),
        FakeClinicApi(results={"book_appointment": {"record": {"id": "A1"}}}),
        **BOOKING_STAGE,
    )

    tool_call = next(e for e in bus.events if e[1] == "tool_call")
    tool_result = next(e for e in bus.events if e[1] == "tool_result")
    assert tool_call[2]["name"] == "book_appointment"
    assert tool_result[2]["tool_call_id"] == tool_call[2]["tool_call_id"]
    assert tool_result[2]["status"] == 200
    assert tool_result[2]["response"] == {"record": {"id": "A1"}}

    update = next(u for u in bus.updates if "outcome" in u[1])
    assert update[1]["outcome"] == "BOOK"
    assert update[1]["patient_id"] == "P00042"

    assert [s[1] for s in repository.submissions] == ["BOOK"]
    submission = repository.submissions[0]
    assert submission[3] == 200
    assert submission[2]["patient_id"] == "P00042"
    assert len(responses) == 2


def test_turn_book_failure_records_no_outcome_or_submission(monkeypatch, bus):
    _responses, repository, _graph = run_turn(
        monkeypatch,
        AgentResponse(
            immediate_answer="Un momento.",
            tool_calls=[ToolCall(name="book_appointment", arguments=BOOK_ARGS)],
        ),
        FakeClinicApi(fail_with=ProsperApiError(422, {"detail": "slot taken"})),
        **BOOKING_STAGE,
    )

    tool_result = next(e for e in bus.events if e[1] == "tool_result")
    assert tool_result[2]["status"] == 422
    assert "422" in tool_result[2]["error"]
    assert not any(e[1] == "submit" for e in bus.events)
    assert not any("outcome" in fields for _, fields in bus.updates)
    assert repository.submissions == []
    # The provider's own wording never reaches the model, only the status.
    assert "slot taken" not in tool_result[2]["error"]


def test_turn_search_identifies_patient_without_submission(monkeypatch, bus):
    responses, repository, _graph = run_turn(
        monkeypatch,
        AgentResponse(
            immediate_answer="Ya la tengo.",
            tool_calls=[ToolCall(name="search_patients", arguments={"name": "Ana"})],
        ),
        FakeClinicApi(
            results={
                "search_patients": {
                    "matches": [
                        {
                            "patient_id": "P00042",
                            "given_name": "Ana",
                            "first_surname": "García",
                            "second_surname": "López",
                            "insurer": "sanitas",
                        }
                    ]
                }
            }
        ),
    )

    update = next(u for u in bus.updates if "patient_id" in u[1])
    assert update[1]["patient_id"] == "P00042"
    assert update[1]["patient_name"] == "Ana García López"
    assert repository.submissions == []
    assert len(responses) == 2


def test_turn_no_action_records_reason_and_outcome(monkeypatch, bus):
    responses, repository, _graph = run_turn(
        monkeypatch,
        AgentResponse(
            immediate_answer="No hay huecos.",
            tool_calls=[
                ToolCall(
                    name="submit_no_action",
                    arguments={"reason": "no_availability"},
                )
            ],
        ),
        FakeClinicApi(),
    )

    update = next(u for u in bus.updates if "outcome" in u[1])
    assert update[1]["outcome"] == "NO_ACTION"
    assert update[1]["reason"] == "no_availability"
    assert [s[1] for s in repository.submissions] == ["NO_ACTION"]
    assert len(responses) == 2


def test_immediate_response_can_be_skipped_before_tts(monkeypatch, bus):
    monkeypatch.setenv("SEND_IMMEDIATE_RESPONSES", "false")
    responses, repository, _graph = run_turn(
        monkeypatch,
        AgentResponse(
            immediate_answer="Let me check.",
            tool_calls=[
                ToolCall(
                    name="submit_no_action",
                    arguments={"reason": "no_availability"},
                )
            ],
        ),
        FakeClinicApi(),
    )

    assert [response.immediate_answer for response in responses] == ["Hecho."]
    assert not any(
        event == "agent_response" and payload["text"] == "Let me check."
        for _, event, payload in repository.events
    )


def test_turn_book_is_blocked_before_identification(monkeypatch, bus):
    """The entry stage has no booking tool, so the call never reaches Prosper."""
    responses, repository, _graph = run_turn(
        monkeypatch,
        AgentResponse(
            immediate_answer="Un momento.",
            tool_calls=[ToolCall(name="book_appointment", arguments=BOOK_ARGS)],
        ),
        FakeClinicApi(),
    )

    assert not any(event[1] == "tool_result" for event in bus.events)
    rejected = next(event for event in bus.events if event[1] == "tool_rejected")
    assert rejected[2]["requested"] == "book_appointment"
    assert not any(event[1] == "submit" for event in bus.events)
    assert not any("outcome" in fields for _, fields in bus.updates)
    assert repository.submissions == []
    # The promise was never spoken, because the operation behind it was refused.
    assert "Un momento." not in [item.immediate_answer for item in responses]


def test_turn_identifies_then_books_without_the_caller_speaking_again(monkeypatch, bus):
    """Identification and the action land in one caller turn, but not one batch.

    The booking arguments in a lookup's own batch were written before the lookup
    ran, so its patient_id could only have been invented. The runtime refuses it
    and the model books on the next step, from the result it can now see.
    """
    api = FakeClinicApi(
        results={
            "search_patients": {"matches": [{"patient_id": "P00042", "given_name": "Ana"}]},
            "book_appointment": {"record": {"id": "A1"}},
        }
    )
    monkeypatch.setattr(ClinicApi, "from_environment", classmethod(lambda cls: api))

    steps = [
        [ToolCall(name="search_patients", arguments={"name": "Ana García"}),
         ToolCall(name="book_appointment", arguments=BOOK_ARGS)],
        [ToolCall(name="record_facts", arguments={"facts": {"patient_id": "P00042"}}),
         ToolCall(name="go_to", arguments={"stage": "atender"})],
        [ToolCall(name="record_facts", arguments={"facts": {"slot_elegido": BOOK_ARGS["slot"]}}),
         ToolCall(name="go_to", arguments={"stage": "reservar"})],
        [ToolCall(name="book_appointment", arguments=BOOK_ARGS)],
    ]

    class ScriptedClient(FakeTurnClient):
        def __init__(self):
            self.remaining = list(steps)

        def complete_with_tools(self, prompt, tools, **kwargs):
            calls = self.remaining.pop(0) if self.remaining else []
            return ToolCompletion(
                text="Un momento.",
                tool_calls=[
                    LLMToolCall(call_id=str(i), name=c.name, arguments=c.arguments)
                    for i, c in enumerate(calls)
                ],
                usage=Usage(),
            )

    repository = FakeRepository()
    graph = CallGraph.start()

    async def collect():
        return [
            item
            async for item in run_agent_turn(
                "hola", "CA456", cast(Any, repository), cast(Any, ScriptedClient()), state=graph
            )
        ]

    asyncio.run(collect())

    assert graph.stage_id == "reservar"
    assert graph.facts["patient_id"] == "P00042"
    assert [submission[1] for submission in repository.submissions] == ["BOOK"]
    update = next(u for u in bus.updates if "outcome" in u[1])
    assert update[1]["outcome"] == "BOOK"
