"""Orchestration for the booking agent."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING
import logging
from pathlib import Path
from typing import Any

import yaml

from agent.llm import LLMClient
from agent.models import AgentResponse, ToolResult
from agent.tools import clinic_hours, find_available_slots, load_tools
from agent.utils import configure_logging

if TYPE_CHECKING:
    from storage import CallRepository

_logger = logging.getLogger(__name__)


def retrieve_memory() -> str:
    """Compatibility helper for the command-line agent."""
    return ""


def _system_prompt() -> str:
    prompt_file = Path(__file__).with_name("prompts.yaml")
    with prompt_file.open(encoding="utf-8") as file:
        return yaml.safe_load(file)["system"]


def run_agent(prompt: str, client: LLMClient | None = None) -> Iterator[str | ToolResult]:
    """Yield the immediate answer, then yield each tool result as it completes."""
    configure_logging()
    _logger.info("Starting agent run")
    _logger.debug("Caller prompt: %s", prompt)
    tools = load_tools([find_available_slots, clinic_hours])
    message = (
        f"System prompt:\n{_system_prompt()}\n\n"
        f"Memory:\n{retrieve_memory()}\n\n"
        f"Caller input:\n{prompt}"
    )
    completion = (client or LLMClient()).complete_structured(
        message,
        AgentResponse,
        extra_body={"tools": [tool["definition"] for tool in tools.values()]},
    )
    response = completion.data
    _logger.info("Agent produced immediate answer and %d tool call(s)", len(response.tool_calls))
    _logger.debug("Structured agent response: %s", response.model_dump())
    yield response.immediate_answer

    for call in response.tool_calls:
        _logger.info("Executing tool=%s", call.name)
        _logger.debug("Tool arguments for %s: %s", call.name, call.arguments)
        tool = tools.get(call.name)
        if tool is None:
            _logger.warning("Unknown tool requested: %s", call.name)
            yield ToolResult(name=call.name, output=f"Unknown tool: {call.name}")
            continue
        try:
            output: Any = tool["execute"](**call.arguments)
        except (TypeError, ValueError) as exc:
            _logger.exception("Tool execution failed: %s", call.name)
            output = f"Tool error: {exc}"
        _logger.info("Tool completed: %s", call.name)
        _logger.debug("Tool output for %s: %s", call.name, output)
        yield ToolResult(name=call.name, output=output)


async def run_agent_for_call(
    prompt: str,
    call_id: str,
    repository: "CallRepository",
    client: LLMClient | None = None,
) -> AgentResponse:
    """Run one turn and persist the conversation and tool results."""
    _logger.info("starting call agent | call_id=%s prompt=%r", call_id, prompt)
    await repository.append_event(call_id, "caller_text_received", {"text": prompt})
    memory = await repository.memory_for_call(call_id)
    try:
        response = (client or LLMClient()).complete_structured(
            f"System prompt:\n{_system_prompt()}\n\nMemory:\n{memory}\n\nCaller input:\n{prompt}",
            AgentResponse,
            extra_body={
                "tools": [
                    tool["definition"]
                    for tool in load_tools([find_available_slots, clinic_hours]).values()
                ]
            },
        ).data
    except Exception:
        _logger.exception("LLM request failed | call_id=%s", call_id)
        raise
    await repository.append_event(
        call_id,
        "agent_response",
        {"text": response.immediate_answer},
    )
    for call in response.tool_calls:
        await repository.append_event(
            call_id,
            "tool_call",
            {"name": call.name, "arguments": call.arguments},
        )
        tool = load_tools([find_available_slots, clinic_hours]).get(call.name)
        output = f"Unknown tool: {call.name}"
        if tool:
            try:
                output = tool["execute"](**call.arguments)
            except (TypeError, ValueError) as exc:
                output = f"Tool error: {exc}"
        await repository.append_event(
            call_id, "tool_result", {"name": call.name, "output": output}
        )
        await repository.record_submission(
            call_id, call.name.upper(), call.arguments, 200, {"output": output}
        )
    return response
