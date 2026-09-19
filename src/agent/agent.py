"""Orchestration for the booking agent."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

import yaml
from pipecat.frames.frames import SystemFrame

from agent.clinic_api import ClinicApi, ProsperApiError
from agent.llm import LLMClient, ToolCompletion, get_llm_client
from agent.models import AgentResponse, ToolCall, ToolResult
from agent.tools import create_clinic_tools, load_tools
from agent.utils import configure_logging
from observability.frames import (
    LLMRequestFailedFrame,
    LLMRequestStartedFrame,
    LLMResponseFinishedFrame,
    ToolCallFinishedFrame,
    ToolCallStartedFrame,
)

if TYPE_CHECKING:
    from storage import CallRepository

_logger = logging.getLogger(__name__)
SENSITIVE_ARGUMENT_MARKERS = {
    "api_key",
    "authorization",
    "call_id",
    "header",
    "password",
    "secret",
    "token",
}
EventSink = Callable[[SystemFrame], Awaitable[None]]
TOOL_ACKNOWLEDGEMENT = "Let me check that for you."
MAX_TOOL_ROUNDS = 3
CLINIC_TIMEZONE = ZoneInfo("Europe/Madrid")


def retrieve_memory() -> str:
    return ""


def _system_prompt() -> str:
    with (Path(__file__).with_name("prompts.yaml")).open(encoding="utf-8") as file:
        return yaml.safe_load(file)["system"]


def _completion_prompt(prompt: str) -> str:
    current_date = datetime.now(CLINIC_TIMEZONE).date().isoformat()
    return (
        f"System prompt:\n{_system_prompt()}\n\n"
        f"Current date in Europe/Madrid: {current_date}\n\n"
        f"Memory:\n{retrieve_memory()}\n\nCaller input:\n{prompt}"
    )


def _completion(
    prompt: str, client: LLMClient | None, tools: dict[str, dict[str, Any]]
) -> AgentResponse:
    completion = (client or get_llm_client()).complete_with_tools(
        _completion_prompt(prompt),
        [tool["definition"] for tool in tools.values()],
    )
    return _agent_response(completion)


def _agent_response(completion: ToolCompletion) -> AgentResponse:
    tool_calls = [
        ToolCall(name=call.name, arguments=call.arguments)
        for call in completion.tool_calls
    ]
    answer = completion.text.strip()
    if tool_calls and not answer:
        answer = TOOL_ACKNOWLEDGEMENT
    if not answer:
        raise ValueError("LLM returned neither text nor tool calls.")
    return AgentResponse(immediate_answer=answer, tool_calls=tool_calls)


async def _observed_tool_completion(
    prompt: str,
    client: LLMClient,
    tools: dict[str, dict[str, Any]],
    event_sink: EventSink | None,
) -> AgentResponse:
    request_id = uuid4().hex
    model = client.default_model
    started_ns = time.perf_counter_ns()
    await _emit(event_sink, LLMRequestStartedFrame(request_id=request_id, model=model))
    try:
        completion = await asyncio.to_thread(
            client.complete_with_tools,
            _completion_prompt(prompt),
            [tool["definition"] for tool in tools.values()],
        )
    except Exception as exc:
        await _emit(
            event_sink,
            LLMRequestFailedFrame(
                request_id=request_id,
                model=model,
                duration_ms=_duration_ms(started_ns),
                error_type=type(exc).__name__,
                error_message="LLM request failed",
            ),
        )
        raise

    usage = completion.usage
    await _emit(
        event_sink,
        LLMResponseFinishedFrame(
            request_id=request_id,
            model=model,
            duration_ms=_duration_ms(started_ns),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cached_tokens=usage.cached_tokens,
        ),
    )
    return _agent_response(completion)


async def _observed_completion(
    prompt: str,
    client: LLMClient,
    tools: dict[str, dict[str, Any]],
    event_sink: EventSink | None,
) -> AgentResponse:
    request_id = uuid4().hex
    model = client.default_model
    started_ns = time.perf_counter_ns()
    await _emit(event_sink, LLMRequestStartedFrame(request_id=request_id, model=model))
    try:
        completion = await asyncio.to_thread(
            client.complete_structured,
            _completion_prompt(prompt),
            AgentResponse,
            extra_body={"tools": [tool["definition"] for tool in tools.values()]},
        )
    except Exception as exc:
        await _emit(
            event_sink,
            LLMRequestFailedFrame(
                request_id=request_id,
                model=model,
                duration_ms=_duration_ms(started_ns),
                error_type=type(exc).__name__,
                error_message="LLM request failed",
            ),
        )
        raise

    usage = completion.usage
    await _emit(
        event_sink,
        LLMResponseFinishedFrame(
            request_id=request_id,
            model=model,
            duration_ms=_duration_ms(started_ns),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cached_tokens=usage.cached_tokens,
        ),
    )
    return cast(AgentResponse, completion.data)


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
    tools = load_tools(
        create_clinic_tools(clinic_api, call_id)
        if clinic_api is not None and call_id is not None
        else []
    )
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
    repository: CallRepository,
    client: LLMClient | None = None,
) -> AgentResponse:
    responses = [
        response
        async for response in run_agent_turn(prompt, call_id, repository, client)
    ]
    return responses[-1]


async def run_agent_turn(
    prompt: str,
    call_id: str,
    repository: CallRepository,
    client: LLMClient | None = None,
    *,
    event_sink: EventSink | None = None,
):
    """Yield the immediate reply, then a reply synthesized from tool results."""
    configure_logging()
    _logger.info("starting call agent | call_id=%s prompt=%r", call_id, prompt)
    await repository.append_event(call_id, "caller_text_received", {"text": prompt})
    memory = await repository.memory_for_call(call_id)
    api = ClinicApi.from_environment()
    llm_client = client or get_llm_client()
    try:
        tools = load_tools(create_clinic_tools(api, call_id))
        response = await _observed_tool_completion(
            f"{prompt}\n\nCall memory:\n{memory}", llm_client, tools, event_sink
        )
        _logger.info(
            "agent response model | call_id=%s response=%s",
            call_id,
            response.model_dump(),
        )
        await repository.append_event(
            call_id, "agent_response", {"text": response.immediate_answer}
        )
        yield response

        tool_history: list[dict[str, Any]] = []
        for tool_round in range(MAX_TOOL_ROUNDS):
            if not response.tool_calls:
                return

            for call in response.tool_calls:
                output = await _execute_tool_call(
                    call, tools.get(call.name), event_sink
                )
                result = {
                    "name": call.name,
                    "arguments": call.arguments,
                    "output": output,
                }
                tool_history.append(result)
                await repository.append_event(
                    call_id,
                    "tool_call",
                    {"name": call.name, "arguments": call.arguments},
                )
                await repository.append_event(
                    call_id, "tool_result", {"name": call.name, "output": output}
                )
                _logger.info(
                    "tool result | call_id=%s tool=%s output=%s",
                    call_id,
                    call.name,
                    output,
                )
                await repository.record_submission(
                    call_id,
                    call.name.upper(),
                    call.arguments,
                    _tool_status_code(output),
                    {"output": output},
                )

            follow_up_prompt = _tool_follow_up_prompt(prompt, memory, tool_history)
            if tool_round == MAX_TOOL_ROUNDS - 1:
                follow_up = await _observed_completion(
                    follow_up_prompt
                    + "\n\nThe tool-call limit is reached. Do not request another tool. "
                    "Explain the problem briefly or ask for the one missing detail.",
                    llm_client,
                    {},
                    event_sink,
                )
            else:
                follow_up = await _observed_tool_completion(
                    follow_up_prompt, llm_client, tools, event_sink
                )

            _logger.info(
                "agent follow-up model | call_id=%s response=%s",
                call_id,
                follow_up.model_dump(),
            )
            if follow_up.tool_calls and tool_round < MAX_TOOL_ROUNDS - 1:
                response = follow_up
                continue
            if follow_up.tool_calls:
                follow_up = follow_up.model_copy(update={"tool_calls": []})

            await repository.append_event(
                call_id,
                "agent_follow_up",
                {"text": follow_up.immediate_answer},
            )
            yield follow_up
            return
    finally:
        await asyncio.to_thread(api.close)


async def _execute_tool_call(
    call: ToolCall,
    tool: dict[str, Any] | None,
    event_sink: EventSink | None,
) -> Any:
    tool_call_id = uuid4().hex
    started_ns = time.perf_counter_ns()
    await _emit(
        event_sink,
        ToolCallStartedFrame(
            tool=call.name,
            tool_call_id=tool_call_id,
            arguments=_safe_arguments(call.arguments),
        ),
    )
    try:
        if tool is None:
            raise LookupError(f"Unknown tool {call.name!r}.")
        output = await asyncio.to_thread(tool["execute"], **call.arguments)
    except Exception as error:  # noqa: BLE001 - errors are returned for correction
        await _emit_tool_error(
            event_sink,
            call.name,
            tool_call_id,
            started_ns,
            type(error).__name__,
            "Tool execution failed",
        )
        return _tool_error_output(error)

    await _emit(
        event_sink,
        ToolCallFinishedFrame(
            tool=call.name,
            tool_call_id=tool_call_id,
            duration_ms=_duration_ms(started_ns),
            status="success",
            result_summary=_result_summary(output),
        ),
    )
    return output


def _tool_error_output(error: Exception) -> dict[str, Any]:
    instruction = (
        "Correct the arguments using the tool schema and call the tool again. "
        "Do not repeat the same invalid arguments."
    )
    if isinstance(error, ProsperApiError):
        return {
            "ok": False,
            "error": "Prosper rejected the tool arguments.",
            "status_code": error.status_code,
            "detail": error.detail,
            "instruction": instruction,
        }
    if isinstance(error, (TypeError, ValueError, LookupError)):
        return {
            "ok": False,
            "error": type(error).__name__,
            "detail": str(error),
            "instruction": instruction,
        }
    return {
        "ok": False,
        "error": type(error).__name__,
        "detail": "The tool failed unexpectedly; no private error details are exposed.",
        "instruction": (
            "Retry once if the call is safe to repeat. If it fails again, tell the "
            "caller the request cannot be completed right now."
        ),
    }


def _tool_follow_up_prompt(
    caller_request: str,
    memory: str,
    tool_history: list[dict[str, Any]],
) -> str:
    return (
        f"Original caller request:\n{caller_request}\n\n"
        f"Call memory:\n{memory}\n\n"
        f"Tool execution history:\n{json.dumps(tool_history, ensure_ascii=False)}\n\n"
        "Continue from the tool results. A result with ok=false is an error, not a "
        "successful action: follow its instruction and retry with corrected arguments. "
        "Never repeat an identical failed call or repeat an action that already succeeded. "
        "If you have enough successful data, answer the caller concisely without mentioning "
        "internal tools. If correction needs caller information, ask one question."
    )


def _tool_status_code(output: Any) -> int:
    if isinstance(output, dict) and output.get("ok") is False:
        status_code = output.get("status_code")
        return status_code if isinstance(status_code, int) else 500
    return 200


async def _emit(event_sink: EventSink | None, frame: SystemFrame) -> None:
    if event_sink is None:
        return
    try:
        await event_sink(frame)
    except Exception:
        _logger.exception("Failed to emit observability event %s", type(frame).__name__)


async def _emit_tool_error(
    event_sink: EventSink | None,
    tool: str,
    tool_call_id: str,
    started_ns: int,
    error_type: str,
    error_message: str,
) -> None:
    await _emit(
        event_sink,
        ToolCallFinishedFrame(
            tool=tool,
            tool_call_id=tool_call_id,
            duration_ms=_duration_ms(started_ns),
            status="error",
            error_type=error_type,
            error_message=error_message,
        ),
    )


def _duration_ms(started_ns: int) -> float:
    return max(1, time.perf_counter_ns() - started_ns) / 1_000_000


def _safe_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _safe_value(value)
        for key, value in arguments.items()
        if not any(marker in key.lower() for marker in SENSITIVE_ARGUMENT_MARKERS)
    }


def _safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _safe_arguments(value)
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _result_summary(result: Any) -> dict[str, Any]:
    if isinstance(result, Mapping):
        return {
            "type": "object",
            "keys": sorted(str(key) for key in result)[:10],
            "item_count": len(result),
        }
    if isinstance(result, list):
        return {"type": "array", "item_count": len(result)}
    if isinstance(result, str):
        return {"type": "string", "length": len(result)}
    return {"type": type(result).__name__}
