"""Orchestration for the booking agent."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import httpx
from pipecat.frames.frames import SystemFrame
from pipecat.transcriptions.language import Language

from agent.call_context import CallContext
from agent.clinic_api import ClinicApi, ProsperApiError
from agent.graph import load_graph
from agent.language import DEFAULT_LANGUAGE, phrases, reply_instruction
from agent.llm import LLMClient, ToolCompletion, get_llm_client
from agent.models import AgentResponse, Tool, ToolCall, ToolResult
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
from agent.tools import ClinicTools, SubmissionRecord, create_clinic_tools, load_tools
from agent.utils import configure_logging
from observability import emit, update_call
from observability.frames import (
    LLMRequestFailedFrame,
    LLMRequestStartedFrame,
    LLMResponseFinishedFrame,
    ToolCallFinishedFrame,
    ToolCallStartedFrame,
)
from scoring import classify_guardrail_breach

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
GUARDRAIL_REFUSAL = (
    "I cannot help with that request. I can help you with your appointment."
)
SUBMISSIONS = {
    "register_patient": ("/api/v1/submit/register", "REGISTER"),
    "submit_no_action": ("/api/v1/submit/no-action", "NO_ACTION"),
    "escalate_to_human": ("/api/v1/submit/escalate", "ESCALATE"),
}
SUBMISSION_ROUTES = {
    "REGISTER": "/api/v1/submit/register",
    "BOOK": "/api/v1/submit/book",
    "RESCHEDULE": "/api/v1/submit/reschedule",
    "CANCEL": "/api/v1/submit/cancel",
    "NO_ACTION": "/api/v1/submit/no-action",
    "ESCALATE": "/api/v1/submit/escalate",
}


def retrieve_memory() -> str:
    return ""


def _completion_prompt(prompt: str) -> str:
    return (
        f"System prompt:\n{load_graph().system}\n\n"
        f"Memory:\n{retrieve_memory()}\n\nCaller input:\n{prompt}"
    )


def _completion(
    prompt: str, client: LLMClient | None, tools: dict[str, Tool]
) -> AgentResponse:
    completion = (client or get_llm_client()).complete_with_tools(
        _completion_prompt(prompt),
        [tool.definition for tool in tools.values()],
    )
    return _agent_response(completion)


def _agent_response(completion: ToolCompletion) -> AgentResponse:
    tool_calls = [
        ToolCall(name=call.name, arguments=call.arguments)
        for call in completion.tool_calls
    ]
    answer = completion.text.strip()
    if tool_calls and not answer:
        answer = phrases(DEFAULT_LANGUAGE).acknowledgement
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
        completion = await asyncio.to_thread(
            client.complete_with_tools, prompt, tool_definitions
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
    return completion


async def _observed_completion(
    prompt: str,
    client: LLMClient,
    event_sink: EventSink | None,
) -> AgentResponse:
    request_id = uuid4().hex
    model = client.default_model
    started_ns = time.perf_counter_ns()
    await _emit(event_sink, LLMRequestStartedFrame(request_id=request_id, model=model))
    try:
        completion = await asyncio.to_thread(
            client.complete_structured, prompt, AgentResponse
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
    context = CallContext(call_id or "standalone")
    context.begin_turn()
    tools = load_tools(
        create_clinic_tools(clinic_api, context.call_id, context) if clinic_api else []
    )
    response = _completion(prompt, client, tools)
    _logger.info(
        "Agent produced immediate answer and %d tool call(s)", len(response.tool_calls)
    )
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
            output = tool.execute(**call.arguments)
        except ProsperApiError as exc:
            elapsed_ms = _elapsed_ms(started)
            _logger.warning(
                "Prosper rejected tool=%s status=%s", call.name, exc.status_code
            )
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
    if (
        not isinstance(matches, list)
        or len(matches) != 1
        or not isinstance(matches[0], dict)
    ):
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


async def run_agent_for_call(
    prompt: str,
    call_id: str,
    repository: CallRepository,
    client: LLMClient | None = None,
    *,
    state: CallGraph | None = None,
) -> AgentResponse:
    responses = [
        response
        async for response in run_agent_turn(
            prompt, call_id, repository, client, state=state
        )
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
    language: Language = DEFAULT_LANGUAGE,
):
    """Run one caller turn as a bounded loop over the call's stage graph."""
    configure_logging()
    _logger.info("starting call agent | call_id=%s prompt=%r", call_id, prompt)
    state = state or CallGraph.start(call_id=call_id)
    if state.context.call_id != call_id:
        raise ValueError("state call_id does not match call_id")
    state.start_turn()
    state.history.append(HistoryEntry(speaker="caller", text=prompt))
    await repository.append_event(call_id, "caller_text_received", {"text": prompt})
    await repository.seed_default_guardrails()
    guardrails = await repository.list_guardrails()
    guardrail_text = "\n".join(
        f"- {row.title}: {row.description or row.text}" for row in guardrails
    )

    api = ClinicApi.from_environment()
    llm_client = client or get_llm_client()
    spoke_this_step = False
    ending = "waiting"
    try:
        clinic_tools = ClinicTools(api, state.context)
        tools = load_tools(clinic_tools.functions())
        for step in range(1, MAX_ACTION_STEPS + 1):
            completion = await _observed_tool_completion(
                _graph_prompt(state, language, guardrail_text),
                llm_client,
                _offered_tools(state, tools),
                event_sink,
            )
            batch = _plan_batch(completion, state, call_id, step)
            api_key = os.getenv("TYPESAFE_API_KEY", "").strip()
            if api_key and guardrails and completion.text.strip():
                async with httpx.AsyncClient(timeout=15) as jev_client:
                    classification = await classify_guardrail_breach(
                        [
                            {"speaker": "patient", "text": prompt},
                            {"speaker": "agent", "text": completion.text},
                        ],
                        [
                            f"{row.title}: {row.description or row.text}"
                            for row in guardrails
                        ],
                        jev_client,
                        api_key,
                    )
                if classification["breached"]:
                    violations = classification["violations"]
                    reason = "; ".join(
                        item["guardrail"].split(":", 1)[0] for item in violations
                    )
                    await repository.append_event(
                        call_id, "guardrail_breach", {"reason": reason}
                    )
                    update_call(
                        call_id,
                        guardrail_breached=1,
                        guardrail_reason=reason,
                        guardrail_violations=json.dumps(violations, ensure_ascii=False),
                    )
                    yield AgentResponse(
                        immediate_answer=GUARDRAIL_REFUSAL, tool_calls=[]
                    )
                    return
            speech = _speech_for(completion, batch.refused, language)
            spoke_this_step = bool(speech)
            if speech:
                state.history.append(HistoryEntry(speaker="agent", text=speech))
                await repository.append_event(
                    call_id, "agent_response", {"text": speech}
                )
                yield AgentResponse(immediate_answer=speech, tool_calls=[])
            if not completion.tool_calls:
                break

            produced_new = await _execute_batch(
                batch,
                state,
                tools,
                clinic_tools,
                call_id,
                repository,
                event_sink,
                step,
            )
            state.history.append(
                HistoryEntry(speaker="agent", operations=batch.operations)
            )
            if batch.refused:
                continue
            if not produced_new:
                break
        else:
            ending = "budget"

        # A turn that ends without speech leaves the caller listening to silence.
        if ending == "budget" or not spoke_this_step:
            answer = await _final_answer(state, llm_client, event_sink, language)
            state.history.append(HistoryEntry(speaker="agent", text=answer))
            await repository.append_event(call_id, "agent_follow_up", {"text": answer})
            yield AgentResponse(immediate_answer=answer, tool_calls=[])
    except Exception:
        ending = "failed"
        raise
    finally:
        emit(
            call_id,
            "turn_finished",
            {"turn": state.turn, "stage": state.stage_id, "ending": ending},
        )
        await asyncio.to_thread(api.close)


def _graph_prompt(
    state: CallGraph, language: Language = DEFAULT_LANGUAGE, guardrails: str = ""
) -> str:
    guardrail_prompt = (
        f"\n\nConfigured guardrails (always follow):\n{guardrails}"
        if guardrails
        else ""
    )
    return (
        f"System prompt:\n{state.graph.system}\n{reply_instruction(language)}{guardrail_prompt}\n\n"
        f"{render_context(state)}"
    )


def _offered_tools(state: CallGraph, tools: dict[str, Tool]) -> list[dict[str, Any]]:
    """The stage's own tools, plus the graph's — go_to only where the stage has exits."""
    offered = [
        tools[name].definition for name in state.allowed_tools() if name in tools
    ]
    for definition in GRAPH_TOOL_DEFINITIONS:
        if definition["function"]["name"] == GO_TO_TOOL and not state.has_exits():
            continue
        offered.append(definition)
    return offered


def _speech_for(
    completion: ToolCompletion, refused: bool, language: Language = DEFAULT_LANGUAGE
) -> str:
    """A draft utterance is only safe once every operation in it was permitted."""
    if refused:
        return ""
    text = completion.text.strip()
    if text:
        return text
    business = [
        call
        for call in completion.tool_calls
        if call.name not in (RECORD_FACTS_TOOL, GO_TO_TOOL)
    ]
    # Bookkeeping-only steps stay silent; a caller should never hear the graph working.
    return phrases(language).acknowledgement if business else ""


@dataclass
class Batch:
    """One completion's tool calls, sorted into what will run and what was refused."""

    operations: list[Operation] = field(default_factory=list)
    business: list[Any] = field(default_factory=list)
    transition: Any = None
    refused: bool = False


def _plan_batch(
    completion: ToolCompletion, state: CallGraph, call_id: str, step: int
) -> Batch:
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
                emit(
                    call_id,
                    "fact_recorded",
                    {
                        "turn": state.turn,
                        "step": step,
                        "stage": state.stage_id,
                        "key": key,
                        "value": value,
                    },
                )
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
    batch.operations.append(
        Operation(call.name, call.arguments, "refused", detail=reason)
    )
    emit(
        call_id,
        "transition_rejected" if call.name == GO_TO_TOOL else "tool_rejected",
        {
            "turn": state.turn,
            "step": step,
            "stage": state.stage_id,
            "requested": call.arguments.get("stage")
            if call.name == GO_TO_TOOL
            else call.name,
            "reason": reason,
        },
    )


async def _execute_batch(
    batch: Batch,
    state: CallGraph,
    tools: dict[str, Tool],
    clinic_tools: ClinicTools,
    call_id: str,
    repository: CallRepository,
    event_sink: EventSink | None,
    step: int,
) -> bool:
    """Run the clinic tools, then commit the transition. Returns whether anything is new."""
    produced_new = False
    for call in batch.business:
        # Planning judged the whole batch before any of it ran. An earlier call in
        # this same batch may have just failed, so the guard runs again here.
        late_refusal = state.refuse_reason(call.name, call.arguments)
        if late_refusal:
            batch.refused = True
            batch.operations.append(
                Operation(call.name, call.arguments, "refused", detail=late_refusal)
            )
            emit(
                call_id,
                "tool_rejected",
                {
                    "turn": state.turn,
                    "step": step,
                    "stage": state.stage_id,
                    "requested": call.name,
                    "reason": late_refusal,
                },
            )
            continue
        operation = await _run_tool(
            call,
            state,
            tools,
            clinic_tools,
            call_id,
            repository,
            event_sink,
            step,
        )
        batch.operations.append(operation)
        if operation.status == "executed":
            state.calls_made.add(state.signature(call.name, call.arguments))
        produced_new = True

    business_succeeded = all(
        operation.status == "executed"
        for operation in batch.operations
        if operation.name not in (RECORD_FACTS_TOOL, GO_TO_TOOL)
    )
    if batch.transition is not None and business_succeeded:
        target = str(batch.transition.arguments["stage"])
        source = state.stage_id
        cleared = state.enter(target)
        batch.operations.append(
            Operation(
                batch.transition.name,
                batch.transition.arguments,
                "executed",
                result=f"entered {target}",
            )
        )
        emit(
            call_id,
            "stage_entered",
            {
                "turn": state.turn,
                "step": step,
                "stage": target,
                "from": source,
                "cleared": cleared,
            },
        )
        await _save_state(repository, call_id, state)
        produced_new = True
    return produced_new


async def _run_tool(
    call: Any,
    state: CallGraph,
    tools: dict[str, Tool],
    clinic_tools: ClinicTools,
    call_id: str,
    repository: CallRepository,
    event_sink: EventSink | None,
    step: int,
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
    emit(
        call_id,
        "tool_call",
        {
            "name": call.name,
            "tool_call_id": tool_call_id,
            "arguments": _safe_arguments(call.arguments),
        },
    )
    tool = tools[call.name]
    try:
        output = await asyncio.to_thread(tool.execute, **call.arguments)
    except asyncio.CancelledError:
        state.context.mark_any_submission_uncertain()
        await _record_submission(repository, call_id, clinic_tools.take_submission())
        await _save_state(repository, call_id, state)
        raise
    except Exception as exc:  # noqa: BLE001 - a failed lookup is feedback, not the end of the turn
        await _emit_tool_error(
            event_sink,
            call.name,
            tool_call_id,
            started_ns,
            type(exc).__name__,
            "Tool execution failed",
        )
        _logger.warning(
            "tool failed | call_id=%s tool=%s error=%s", call_id, call.name, exc
        )
        status = getattr(exc, "status_code", 500)
        emit(
            call_id,
            "tool_result",
            {
                "name": call.name,
                "tool_call_id": tool_call_id,
                "status": status,
                "ms": _duration_ms(started_ns),
                "error": f"the request failed with status {status}",
            },
        )
        await repository.append_event(
            call_id, "tool_result", {"name": call.name, "output": f"Tool error: {exc}"}
        )
        await _record_submission(repository, call_id, clinic_tools.take_submission())
        await _save_state(repository, call_id, state)
        # The request may still have been received, so this turn will not repeat it.
        state.failed_tools.add(call.name)
        # The model sees that it failed, never the provider's own words.
        return Operation(
            call.name, call.arguments, "failed", detail="the request did not complete"
        )

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
    await repository.append_event(
        call_id, "tool_result", {"name": call.name, "output": output}
    )
    emit(
        call_id,
        "tool_result",
        {
            "name": call.name,
            "tool_call_id": tool_call_id,
            "status": 200,
            "ms": _duration_ms(started_ns),
            "response": output,
        },
    )
    await _record_submission(repository, call_id, clinic_tools.take_submission())
    _sync_trusted_facts(state, call.name, output, call_id, step)
    await _save_state(repository, call_id, state)
    if call.name == "search_patients":
        _record_unique_patient(call_id, output)
    return Operation(call.name, call.arguments, "executed", result=output)


async def _record_submission(
    repository: CallRepository,
    call_id: str,
    submission: SubmissionRecord | None,
) -> None:
    if submission is None:
        return
    await repository.record_submission(
        call_id,
        submission.action,
        submission.payload,
        submission.response_status,
        submission.response,
    )
    emit(
        call_id,
        "submit",
        {
            "route": SUBMISSION_ROUTES[submission.action],
            "status": submission.response_status,
            "request": submission.payload,
            "response": submission.response,
        },
    )
    if submission.response_status == 200:
        update_call(
            call_id,
            outcome=submission.action,
            **_submission_fields(submission.action, submission.payload),
        )


def _submission_fields(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if action in ("NO_ACTION", "ESCALATE"):
        fields["reason"] = payload.get("reason")
    if action == "BOOK":
        fields["patient_id"] = payload.get("patient_id")
    if "policy_id" in payload:
        fields["insurer"] = payload["policy_id"]
    if action == "REGISTER":
        fields["patient_name"] = " ".join(
            part
            for part in (
                payload.get("given_name"),
                payload.get("first_surname"),
                payload.get("second_surname"),
            )
            if part
        )
        fields["insurer"] = payload.get("insurer")
    return {key: value for key, value in fields.items() if value is not None}


def _sync_trusted_facts(
    state: CallGraph, tool_name: str, output: Any, call_id: str, step: int
) -> None:
    if tool_name.startswith("prepare_") and isinstance(output, Mapping):
        for key in ("request_id", "proposal_id"):
            value = output.get(key)
            if not isinstance(value, str):
                continue
            state.facts[key] = value
            emit(
                call_id,
                "fact_recorded",
                {
                    "turn": state.turn,
                    "step": step,
                    "stage": state.stage_id,
                    "key": key,
                    "value": value,
                },
            )
    elif tool_name in ("revise_request", "confirm_action"):
        state.facts.pop("proposal_id", None)


async def _save_state(
    repository: CallRepository, call_id: str, state: CallGraph
) -> None:
    projection = state.projection()
    save_workflow = getattr(repository, "save_workflow", None)
    if save_workflow is not None:
        await save_workflow(call_id, projection)
    await repository.append_event(
        call_id, "request_context_updated", state.context.projection()
    )


async def _final_answer(
    state: CallGraph,
    client: LLMClient,
    event_sink: EventSink | None,
    language: Language = DEFAULT_LANGUAGE,
) -> str:
    """The reserved tool-free step: say what happened, using only what is already known."""
    response = await _observed_completion(
        f"{_graph_prompt(state, language)}\n\n"
        "Answer the caller now using only what is above. Be concise, never mention "
        "internal tools or stages, and do not promise anything you have not already done.",
        client,
        event_sink,
    )
    # A structurally valid but blank answer would still leave the caller in silence.
    return response.immediate_answer.strip() or phrases(language).no_answer


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
