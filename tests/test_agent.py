from typing import cast

from agent.agent import run_agent
from agent.clinic_api import ClinicApi
from agent.llm import LLMClient
from agent.models import AgentResponse, ToolCall, ToolResult


class FakeClinicApi:
    def search_patients(self, **criteria):
        return {"matches": [{"patient_id": "P00001", **criteria}]}

    def get_patient_appointments(self, patient_id, when="upcoming"):
        return {"patient_id": patient_id, "when": when, "appointments": []}


class FakeLLMClient:
    def __init__(self):
        self.calls = []

    def complete_structured(self, prompt, schema, **kwargs):
        self.calls.append((prompt, schema, kwargs))
        if len(self.calls) == 1:
            data = AgentResponse(
                immediate_answer="Let me look up your record.",
                tool_calls=[
                    ToolCall(
                        name="search_patients",
                        arguments={"phone": "+34000000000"},
                    )
                ],
            )
        else:
            data = AgentResponse(
                immediate_answer="I found your record. Which day works for you?"
            )
        return type("Completion", (), {"data": data})()


class ChainedFakeLLMClient(FakeLLMClient):
    def complete_structured(self, prompt, schema, **kwargs):
        self.calls.append((prompt, schema, kwargs))
        responses = [
            AgentResponse(
                immediate_answer="Let me find your record.",
                tool_calls=[
                    ToolCall(
                        name="search_patients",
                        arguments={"phone": "+34000000000"},
                    )
                ],
            ),
            AgentResponse(
                immediate_answer="I'll check your existing appointments.",
                tool_calls=[
                    ToolCall(
                        name="get_patient_appointments",
                        arguments={"patient_id": "P00001"},
                    )
                ],
            ),
            AgentResponse(immediate_answer="You have no upcoming appointments."),
        ]
        return type("Completion", (), {"data": responses[len(self.calls) - 1]})()


def test_agent_returns_immediate_answer_tool_result_and_grounded_follow_up():
    client = FakeLLMClient()
    clinic_api = FakeClinicApi()

    events = list(
        run_agent(
            "I need an appointment.",
            cast(LLMClient, client),
            clinic_api=cast(ClinicApi, clinic_api),
            call_id="CA123",
        )
    )

    assert events[0] == "Let me look up your record."
    assert isinstance(events[1], ToolResult)
    assert events[1].name == "search_patients"
    assert events[1].output["matches"][0]["patient_id"] == "P00001"
    assert events[2] == "I found your record. Which day works for you?"
    assert len(client.calls) == 2
    assert "tools" in client.calls[1][2]["extra_body"]
    assert '"patient_id": "P00001"' in client.calls[1][0]


def test_agent_can_chain_tools_that_depend_on_prior_results():
    client = ChainedFakeLLMClient()

    events = list(
        run_agent(
            "What appointments do I have?",
            cast(LLMClient, client),
            clinic_api=cast(ClinicApi, FakeClinicApi()),
            call_id="CA123",
        )
    )

    tool_results = [event for event in events if isinstance(event, ToolResult)]
    assert [result.name for result in tool_results] == [
        "search_patients",
        "get_patient_appointments",
    ]
    assert events[-1] == "You have no upcoming appointments."
    assert len(client.calls) == 3
