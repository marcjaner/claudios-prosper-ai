"""Orchestration for the booking agent."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import yaml
from pipecat.frames.frames import SystemFrame

from agent.clinic_api import ClinicApi
from agent.llm import LLMClient, ToolCompletion, get_llm_client
from agent.models import AgentResponse, ToolCall, ToolResult
from agent.stage_runtime import (
    GO_TO_TOOL,
    GRAPH_TOOL_DEFINITIONS,
    MAX_ACTION_STEPS,
    RECORD_FACTS_TOOL,
    CallGraph,
    HistoryEntry,
    Operation,
    render_context,
)
from agent.tools import create_clinic_tools, load_tools
from agent.utils import configure_logging
from observability import emit
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


def retrieve_memory() -> str:
    return ""


def _system_prompt() -> str:
    with (Path(__file__).with_name("prompts.yaml")).open(encoding="utf-8") as file:
        return yaml.safe_load(file)["system"]


def _completion_prompt(prompt: str) -> str:
    return (
        f"System prompt:\n{_system_prompt()}\n\n"
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
    tool_definitions: list[dict[str, Any]],
    event_sink: EventSink | None,
) -> ToolCompletion:
    request_id = uuid4().hex
    model = client.default_model
    started_ns = time.perf_counter_ns()
    await _emit(event_sink, LLMRequestStartedFrame(request_id=request_id, model=model))
    try:
        completion = client.complete_with_tools(prompt, tool_definitions)
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
    return completion


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
        completion = client.complete_structured(
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
    return completion.data


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
    state: CallGraph | None = None,
):
    """Run one caller turn as a bounded loop over the call's stage graph."""
    configure_logging()
    _logger.info("starting call agent | call_id=%s prompt=%r", call_id, prompt)
    state = state or CallGraph.start()
    state.start_turn()
    state.history.append(HistoryEntry(speaker="caller", text=prompt))
    await repository.append_event(call_id, "caller_text_received", {"text": prompt})

    api = ClinicApi.from_environment()
    llm_client = client or get_llm_client()
    spoke_this_step = False
    ending = "waiting"
    try:
        tools = load_tools(create_clinic_tools(api, call_id))
        for step in range(1, MAX_ACTION_STEPS + 1):
            completion = await _observed_tool_completion(
                _graph_prompt(state), llm_client, _offered_tools(state, tools), event_sink
            )
            batch = _plan_batch(completion, state, call_id, step)
            speech = _speech_for(completion, batch.refused)
            spoke_this_step = bool(speech)
            if speech:
                state.history.append(HistoryEntry(speaker="agent", text=speech))
                await repository.append_event(call_id, "agent_response", {"text": speech})
                yield AgentResponse(immediate_answer=speech, tool_calls=[])
            if not completion.tool_calls:
                break

            produced_new = await _execute_batch(
                batch, state, tools, call_id, repository, event_sink, step
            )
            state.history.append(HistoryEntry(speaker="agent", operations=batch.operations))
            if batch.refused:
                continue
            if not produced_new:
                break
        else:
            ending = "budget"

        # A turn that ends without speech leaves the caller listening to silence.
        if ending == "budget" or not spoke_this_step:
            answer = await _final_answer(state, llm_client, event_sink)
            state.history.append(HistoryEntry(speaker="agent", text=answer))
            await repository.append_event(call_id, "agent_follow_up", {"text": answer})
            yield AgentResponse(immediate_answer=answer, tool_calls=[])
    except Exception:
        ending = "failed"
        raise
    finally:
        emit(call_id, "turn_finished", {"turn": state.turn, "stage": state.stage_id, "ending": ending})
        api.close()


def _graph_prompt(state: CallGraph) -> str:
    return f"System prompt:\n{_system_prompt()}\n\n{render_context(state)}"


def _offered_tools(state: CallGraph, tools: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The stage's own tools, plus the graph's — go_to only where the stage has exits."""
    offered = [tools[name]["definition"] for name in state.allowed_tools() if name in tools]
    for definition in GRAPH_TOOL_DEFINITIONS:
        if definition["function"]["name"] == GO_TO_TOOL and not state.has_exits():
            continue
        offered.append(definition)
    return offered


def _speech_for(completion: ToolCompletion, refused: bool) -> str:
    """A draft utterance is only safe once every operation in it was permitted."""
    if refused:
        return ""
    text = completion.text.strip()
    if text:
        return text
    business = [
        call for call in completion.tool_calls
        if call.name not in (RECORD_FACTS_TOOL, GO_TO_TOOL)
    ]
    # Bookkeeping-only steps stay silent; a caller should never hear the graph working.
    return TOOL_ACKNOWLEDGEMENT if business else ""


@dataclass
class Batch:
    """One completion's tool calls, sorted into what will run and what was refused."""

    operations: list[Operation] = field(default_factory=list)
    business: list[Any] = field(default_factory=list)
    transition: Any = None
    refused: bool = False


def _plan_batch(completion: ToolCompletion, state: CallGraph, call_id: str, step: int) -> Batch:
    """Refuse what the stage forbids and apply fact writes, before anything is spoken.

    Fact writes are internal state with no outside effect, so they happen here: a
    transition is only legal against the facts the same batch just recorded.
    """
    batch = Batch()
    transitions = []
    for call in completion.tool_calls:
        if call.name == GO_TO_TOOL:
            transitions.append(call)
            continue
        reason = state.refuse_reason(call.name, call.arguments)
        if reason:
            _refuse(batch, state, call, reason, call_id, step)
        elif call.name == RECORD_FACTS_TOOL:
            written = state.record_facts(call.arguments)
            batch.operations.append(
                Operation(call.name, call.arguments, "executed", result="recorded")
            )
            for key, value in written.items():
                emit(call_id, "fact_recorded", {
                    "turn": state.turn, "step": step, "stage": state.stage_id,
                    "key": key, "value": value,
                })
        else:
            batch.business.append(call)

    for index, call in enumerate(transitions):
        reason = state.refuse_reason(call.name, call.arguments)
        if index and not reason:
            reason = "only one transition per step"
        if reason:
            _refuse(batch, state, call, reason, call_id, step)
        else:
            batch.transition = call
    return batch


def _refuse(
    batch: Batch, state: CallGraph, call: Any, reason: str, call_id: str, step: int
) -> None:
    batch.refused = True
    batch.operations.append(Operation(call.name, call.arguments, "refused", detail=reason))
    emit(call_id, "transition_rejected" if call.name == GO_TO_TOOL else "tool_rejected", {
        "turn": state.turn, "step": step, "stage": state.stage_id,
        "requested": call.arguments.get("stage") if call.name == GO_TO_TOOL else call.name,
        "reason": reason,
    })


async def _execute_batch(
    batch: Batch,
    state: CallGraph,
    tools: dict[str, dict[str, Any]],
    call_id: str,
    repository: CallRepository,
    event_sink: EventSink | None,
    step: int,
) -> bool:
    """Run the clinic tools, then commit the transition. Returns whether anything is new."""
    produced_new = False
    for call in batch.business:
        batch.operations.append(
            await _run_tool(call, state, tools, call_id, repository, event_sink)
        )
        produced_new = True

    if batch.transition is not None:
        target = str(batch.transition.arguments["stage"])
        source = state.stage_id
        cleared = state.enter(target)
        batch.operations.append(
            Operation(batch.transition.name, batch.transition.arguments, "executed",
                      result=f"entered {target}")
        )
        emit(call_id, "stage_entered", {
            "turn": state.turn, "step": step, "stage": target,
            "from": source, "cleared": cleared,
        })
        produced_new = True
    return produced_new


async def _run_tool(
    call: Any,
    state: CallGraph,
    tools: dict[str, dict[str, Any]],
    call_id: str,
    repository: CallRepository,
    event_sink: EventSink | None,
) -> Operation:
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
    tool = tools[call.name]
    try:
        output = tool["execute"](**call.arguments)
    except Exception as exc:  # noqa: BLE001 - a failed lookup is feedback, not the end of the turn
        await _emit_tool_error(
            event_sink, call.name, tool_call_id, started_ns, type(exc).__name__,
            "Tool execution failed",
        )
        _logger.warning("tool failed | call_id=%s tool=%s error=%s", call_id, call.name, exc)
        await repository.append_event(
            call_id, "tool_result", {"name": call.name, "output": f"Tool error: {exc}"}
        )
        # The request may still have been received, so this turn will not repeat it.
        state.failed_tools.add(call.name)
        # The model sees that it failed, never the provider's own words.
        return Operation(call.name, call.arguments, "failed", detail="the request did not complete")

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
    await repository.append_event(
        call_id, "tool_call", {"name": call.name, "arguments": call.arguments}
    )
    await repository.append_event(call_id, "tool_result", {"name": call.name, "output": output})
    await repository.record_submission(
        call_id, call.name.upper(), call.arguments, 200, {"output": output}
    )
    return Operation(call.name, call.arguments, "executed", result=output)


async def _final_answer(
    state: CallGraph, client: LLMClient, event_sink: EventSink | None
) -> str:
    """The reserved tool-free step: say what happened, using only what is already known."""
    request_id = uuid4().hex
    started_ns = time.perf_counter_ns()
    await _emit(event_sink, LLMRequestStartedFrame(request_id=request_id, model=client.default_model))
    try:
        completion = client.complete(
            f"{_graph_prompt(state)}\n\n"
            "Answer the caller now using only what is above. Be concise, never mention "
            "internal tools or stages, and do not promise anything you have not already done."
        )
    except Exception as exc:
        await _emit(
            event_sink,
            LLMRequestFailedFrame(
                request_id=request_id,
                model=client.default_model,
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
            model=client.default_model,
            duration_ms=_duration_ms(started_ns),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cached_tokens=usage.cached_tokens,
        ),
    )
    return completion.text.strip()


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
