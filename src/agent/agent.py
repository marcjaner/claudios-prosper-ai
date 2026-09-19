"""Orchestration for the booking agent."""

from __future__ import annotations

from collections.abc import Iterator
import logging
from pathlib import Path
from typing import Any

import yaml

from agent.llm import LLMClient
from agent.models import AgentResponse, ToolResult
from agent.tools import clinic_hours, find_available_slots, load_tools
from agent.utils import configure_logging

_logger = logging.getLogger(__name__)


def retrieve_memory() -> str:
    """Return conversation memory (currently a placeholder)."""
    _logger.debug("Retrieving conversation memory")
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
