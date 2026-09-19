"""Orchestration for the booking agent."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from agent.clinic_api import ClinicApi
from agent.llm import LLMClient, get_llm_client
from agent.models import AgentResponse, ToolResult
from agent.tools import create_clinic_tools, load_tools
from agent.utils import configure_logging

if TYPE_CHECKING:
    from storage import CallRepository

_logger = logging.getLogger(__name__)


def retrieve_memory() -> str:
    return ""


def _system_prompt() -> str:
    with (Path(__file__).with_name("prompts.yaml")).open(encoding="utf-8") as file:
        return yaml.safe_load(file)["system"]


def _completion(prompt: str, client: LLMClient | None, tools: dict[str, dict[str, Any]]) -> AgentResponse:
    message = f"System prompt:\n{_system_prompt()}\n\nMemory:\n{retrieve_memory()}\n\nCaller input:\n{prompt}"
    return (client or get_llm_client()).complete_structured(
        message,
        AgentResponse,
        extra_body={"tools": [tool["definition"] for tool in tools.values()]},
    ).data


def run_agent(
    prompt: str,
    client: LLMClient | None = None,
    *,
    clinic_api: ClinicApi | None = None,
    call_id: str | None = None,
) -> Iterator[str | ToolResult]:
    configure_logging()
    if (clinic_api is None) != (call_id is None):
        raise ValueError("clinic_api and call_id must be provided together")
    tools = load_tools(create_clinic_tools(clinic_api, call_id) if clinic_api else [])
    response = _completion(prompt, client, tools)
    yield response.immediate_answer
    for call in response.tool_calls:
        tool = tools.get(call.name)
        if tool is None:
            yield ToolResult(name=call.name, output=f"Unknown tool: {call.name}")
            continue
        try:
            output = tool["execute"](**call.arguments)
        except (TypeError, ValueError) as exc:
            output = f"Tool error: {exc}"
        yield ToolResult(name=call.name, output=output)


async def run_agent_for_call(
    prompt: str,
    call_id: str,
    repository: "CallRepository",
    client: LLMClient | None = None,
) -> AgentResponse:
    responses = [response async for response in run_agent_turn(prompt, call_id, repository, client)]
    return responses[-1]


async def run_agent_turn(
    prompt: str,
    call_id: str,
    repository: "CallRepository",
    client: LLMClient | None = None,
):
    """Yield the immediate reply, then a reply synthesized from tool results."""
    configure_logging()
    _logger.info("starting call agent | call_id=%s prompt=%r", call_id, prompt)
    await repository.append_event(call_id, "caller_text_received", {"text": prompt})
    memory = await repository.memory_for_call(call_id)
    api = ClinicApi.from_environment()
    try:
        tools = load_tools(create_clinic_tools(api, call_id))
        response = _completion(f"{prompt}\n\nCall memory:\n{memory}", client, tools)
        _logger.info("agent response model | call_id=%s response=%s", call_id, response.model_dump())
        await repository.append_event(call_id, "agent_response", {"text": response.immediate_answer})
        yield response
        tool_results = []
        for call in response.tool_calls:
            tool = tools.get(call.name)
            output: Any = f"Unknown tool: {call.name}"
            if tool:
                try:
                    output = tool["execute"](**call.arguments)
                except (TypeError, ValueError) as exc:
                    output = f"Tool error: {exc}"
            await repository.append_event(call_id, "tool_call", {"name": call.name, "arguments": call.arguments})
            await repository.append_event(call_id, "tool_result", {"name": call.name, "output": output})
            _logger.info("tool result | call_id=%s tool=%s output=%s", call_id, call.name, output)
            await repository.record_submission(call_id, call.name.upper(), call.arguments, 200, {"output": output})
            tool_results.append({"name": call.name, "arguments": call.arguments, "output": output})
        if tool_results:
            follow_up = _completion(
                f"Original caller request:\n{prompt}\n\nTool results:\n{tool_results}\n\n"
                "Answer the caller using only these tool results. Be concise and do not mention internal tools.",
                client,
                {},
            )
            _logger.info("agent follow-up model | call_id=%s response=%s", call_id, follow_up.model_dump())
            await repository.append_event(call_id, "agent_follow_up", {"text": follow_up.immediate_answer})
            yield follow_up
    finally:
        api.close()
