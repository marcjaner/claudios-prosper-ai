"""Orchestration for the booking agent."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import yaml

from agent.clinic_api import ClinicApi, ProsperApiError
from agent.llm import LLMClient
from agent.models import AgentResponse, ToolResult
from agent.tools import create_clinic_tools, load_tools
from agent.utils import configure_logging

_logger = logging.getLogger(__name__)
MAX_TOOL_ROUNDS = 4


def retrieve_memory() -> str:
    """Return conversation memory (currently a placeholder)."""
    _logger.debug("Retrieving conversation memory")
    return ""


def _system_prompt() -> str:
    prompt_file = Path(__file__).with_name("prompts.yaml")
    with prompt_file.open(encoding="utf-8") as file:
        return yaml.safe_load(file)["system"]


def run_agent(
    prompt: str,
    client: LLMClient | None = None,
    *,
    clinic_api: ClinicApi | None = None,
    call_id: str | None = None,
) -> Iterator[str | ToolResult]:
    """Yield the acknowledgement, tool results, and grounded follow-up in order."""
    configure_logging()
    _logger.info("Starting agent run")
    _logger.debug("Caller prompt: %s", prompt)
    if (clinic_api is None) != (call_id is None):
        raise ValueError("clinic_api and call_id must be provided together")
    client = client or LLMClient()
    tool_functions = (
        create_clinic_tools(clinic_api, call_id)
        if clinic_api is not None and call_id is not None
        else []
    )
    tools = load_tools(tool_functions)
    message = (
        f"System prompt:\n{_system_prompt()}\n\n"
        f"Memory:\n{retrieve_memory()}\n\n"
        f"Caller input:\n{prompt}"
    )
    tool_definitions = [tool["definition"] for tool in tools.values()]
    response = client.complete_structured(
        message,
        AgentResponse,
        extra_body={"tools": tool_definitions},
    ).data

    results: list[ToolResult] = []
    answers: list[str] = []
    for round_index in range(MAX_TOOL_ROUNDS):
        _logger.info(
            "Agent produced answer and %d tool call(s)", len(response.tool_calls)
        )
        _logger.debug("Structured agent response: %s", response.model_dump())
        answers.append(response.immediate_answer)
        yield response.immediate_answer
        if not response.tool_calls:
            return

        for result in _execute_tools(response, tools):
            results.append(result)
            yield result

        is_last_round = round_index == MAX_TOOL_ROUNDS - 1
        follow_up_prompt = _follow_up_prompt(message, answers, results, is_last_round)
        if is_last_round:
            response = client.complete_structured(follow_up_prompt, AgentResponse).data
        else:
            response = client.complete_structured(
                follow_up_prompt,
                AgentResponse,
                extra_body={"tools": tool_definitions},
            ).data

    yield response.immediate_answer


def _execute_tools(response: AgentResponse, tools: dict) -> Iterator[ToolResult]:
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
        except (httpx.HTTPError, ProsperApiError, TypeError, ValueError) as exc:
            _logger.exception("Tool execution failed: %s", call.name)
            output = f"Tool error: {exc}"
        _logger.info("Tool completed: %s", call.name)
        _logger.debug("Tool output for %s: %s", call.name, output)
        yield ToolResult(name=call.name, output=output)


def _follow_up_prompt(
    original_message: str,
    answers: list[str],
    results: list[ToolResult],
    force_final_answer: bool,
) -> str:
    tool_results = json.dumps(
        [result.model_dump() for result in results],
        default=str,
        ensure_ascii=False,
    )
    return (
        f"{original_message}\n\n"
        f"Assistant messages so far:\n{json.dumps(answers, ensure_ascii=False)}\n\n"
        f"Tool results:\n{tool_results}\n\n"
        "Continue resolving the caller's request using these verified results. "
        "Do not repeat completed tool calls. Ask only for information still needed. "
        + (
            "Give a concise grounded answer and return an empty tool_calls list."
            if force_final_answer
            else "Request another tool only when it is necessary for the next step."
        )
    )
