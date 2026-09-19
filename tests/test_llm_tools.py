from types import SimpleNamespace
from typing import cast

from agent.clinic_api import ClinicApi
from agent.llm import LLMClient
from agent.tools import create_clinic_tools, load_tools


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def test_complete_with_tools_sends_and_parses_native_function_calls(monkeypatch):
    function = SimpleNamespace(
        name="search_patients",
        arguments='{"name":"Lucas Jones Smith"}',
    )
    message = SimpleNamespace(
        content=None,
        tool_calls=[SimpleNamespace(id="call-1", type="function", function=function)],
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=None,
    )
    completions = FakeCompletions(response)
    openai_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    client = LLMClient(api_key="test-key")
    monkeypatch.setattr(client, "_get_client", lambda _model: openai_client)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_patients",
                "description": "Find a patient.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    completion = client.complete_with_tools("Find Lucas", tools)

    assert completions.kwargs is not None
    assert completions.kwargs["tools"] == tools
    assert completions.kwargs["tool_choice"] == "auto"
    assert "response_format" not in completions.kwargs
    assert completion.text == ""
    assert completion.tool_calls[0].call_id == "call-1"
    assert completion.tool_calls[0].name == "search_patients"
    assert completion.tool_calls[0].arguments == {"name": "Lucas Jones Smith"}


def test_every_clinic_tool_has_a_description():
    tools = load_tools(create_clinic_tools(cast(ClinicApi, object()), "CA456"))

    assert tools
    assert all(tool["definition"]["function"]["description"] for tool in tools.values())
