"""Orchestration for the booking agent."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from agent.clinic_api import ClinicApi, ProsperApiError
from agent.llm import DEFAULT_MODEL, LLMClient
from agent.models import AgentResponse, ToolResult
from agent.tools import create_clinic_tools, load_tools
from agent.utils import configure_logging
from observability import emit, update_call

_logger = logging.getLogger(__name__)

SUBMISSIONS = {
    "register_patient": ("/api/v1/submit/register", "REGISTER"),
    "book_appointment": ("/api/v1/submit/book", "BOOK"),
    "reschedule_appointment": ("/api/v1/submit/reschedule", "RESCHEDULE"),
    "cancel_appointment": ("/api/v1/submit/cancel", "CANCEL"),
    "submit_no_action": ("/api/v1/submit/no-action", "NO_ACTION"),
    "escalate_to_human": ("/api/v1/submit/escalate", "ESCALATE"),
}


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
    """Yield the immediate answer, then yield each tool result as it completes."""
    configure_logging()
    _logger.info("Starting agent run")
    _logger.debug("Caller prompt: %s", prompt)
    if (clinic_api is None) != (call_id is None):
        raise ValueError("clinic_api and call_id must be provided together")
    tools = load_tools(create_clinic_tools(clinic_api, call_id) if clinic_api else [])
    message = (
        f"System prompt:\n{_system_prompt()}\n\n"
        f"Memory:\n{retrieve_memory()}\n\n"
        f"Caller input:\n{prompt}"
    )
    completion = (
        client or LLMClient(os.getenv("HELMCODE_MODEL", DEFAULT_MODEL))
    ).complete_structured(
        message,
        AgentResponse,
        extra_body={"tools": [tool["definition"] for tool in tools.values()]},
    )
    response = completion.data
    _logger.info("Agent produced immediate answer and %d tool call(s)", len(response.tool_calls))
    _logger.debug("Structured agent response: %s", response.model_dump())
    yield response.immediate_answer

    for index, call in enumerate(response.tool_calls):
        _logger.info("Executing tool=%s", call.name)
        _logger.debug("Tool arguments for %s: %s", call.name, call.arguments)
        tool_call_id = f"{call.name}-{index}"
        if call_id:
            emit(
                call_id,
                "tool_call",
                {
                    "name": call.name,
                    "tool_call_id": tool_call_id,
                    "arguments": call.arguments,
                },
            )
        tool = tools.get(call.name)
        if tool is None:
            _logger.warning("Unknown tool requested: %s", call.name)
            output = f"Unknown tool: {call.name}"
            if call_id:
                emit(
                    call_id,
                    "tool_result",
                    {
                        "name": call.name,
                        "tool_call_id": tool_call_id,
                        "status": 404,
                        "ms": 0,
                        "error": output,
                    },
                )
            yield ToolResult(name=call.name, output=output)
            continue
        started = time.monotonic()
        try:
            output = tool["execute"](**call.arguments)
        except ProsperApiError as exc:
            elapsed_ms = _elapsed_ms(started)
            _logger.warning("Prosper rejected tool=%s status=%s", call.name, exc.status_code)
            output = f"Prosper API error {exc.status_code}: {exc.detail}"
            if call_id:
                emit(
                    call_id,
                    "tool_result",
                    {
                        "name": call.name,
                        "tool_call_id": tool_call_id,
                        "status": exc.status_code,
                        "ms": elapsed_ms,
                        "error": output,
                    },
                )
        except (TypeError, ValueError) as exc:
            elapsed_ms = _elapsed_ms(started)
            _logger.exception("Tool execution failed: %s", call.name)
            output = f"Tool error: {exc}"
            if call_id:
                emit(
                    call_id,
                    "tool_result",
                    {
                        "name": call.name,
                        "tool_call_id": tool_call_id,
                        "status": 500,
                        "ms": elapsed_ms,
                        "error": output,
                    },
                )
        else:
            elapsed_ms = _elapsed_ms(started)
            _logger.info("Tool completed: %s", call.name)
            _logger.debug("Tool output for %s: %s", call.name, output)
            if call_id:
                emit(
                    call_id,
                    "tool_result",
                    {
                        "name": call.name,
                        "tool_call_id": tool_call_id,
                        "status": 200,
                        "ms": elapsed_ms,
                        "response": output,
                    },
                )
                _record_success(call_id, call.name, call.arguments, output)
        yield ToolResult(name=call.name, output=output)


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _record_success(call_id: str, name: str, arguments: dict, output: Any) -> None:
    if name in SUBMISSIONS:
        route, outcome = SUBMISSIONS[name]
        emit(
            call_id,
            "submit",
            {"route": route, "status": 200, "request": arguments, "response": output},
        )
        update_call(call_id, outcome=outcome, **_call_fields(name, arguments))
    elif name == "search_patients":
        _record_unique_patient(call_id, output)


def _call_fields(name: str, arguments: dict) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if name in ("submit_no_action", "escalate_to_human"):
        reason = arguments.get("reason")
        fields["reason"] = getattr(reason, "value", reason)
    if name == "book_appointment":
        fields["patient_id"] = arguments.get("patient_id")
    if "policy_id" in arguments:
        fields["insurer"] = arguments["policy_id"]
    if name == "register_patient":
        fields["patient_name"] = " ".join(
            part
            for part in (
                arguments.get("given_name"),
                arguments.get("first_surname"),
                arguments.get("second_surname"),
            )
            if part
        )
        fields["insurer"] = arguments.get("insurer")
    return {key: value for key, value in fields.items() if value is not None}


def _record_unique_patient(call_id: str, output: Any) -> None:
    if not isinstance(output, dict):
        return
    matches = output.get("matches")
    if not isinstance(matches, list) or len(matches) != 1 or not isinstance(matches[0], dict):
        return
    match = matches[0]
    fields: dict[str, Any] = {}
    patient_id = match.get("patient_id") or match.get("id")
    if patient_id:
        fields["patient_id"] = patient_id
    if match.get("insurer"):
        fields["insurer"] = match["insurer"]
    patient_name = match.get("name") or " ".join(
        part
        for part in (
            match.get("given_name"),
            match.get("first_surname"),
            match.get("second_surname"),
        )
        if part
    )
    if patient_name:
        fields["patient_name"] = patient_name
    if fields:
        update_call(call_id, **fields)
