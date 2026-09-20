import asyncio

import pytest

from agent.agent import run_agent_turn
from agent.graph import InvalidGraph, parse_graph
from agent.language import DEFAULT_LANGUAGE, phrases
from agent.llm import LLMToolCall, ToolCompletion, Usage
from agent.stage_runtime import CallGraph

IDENTIFY = {
    "id": "identify",
    "prompt": "Find out who is calling.",
    "tools": ["search_patients"],
}
BOOK = {
    "id": "book",
    "prompt": "Book the appointment.",
    "tools": ["book_appointment", "search_availability"],
    "clears": ["slot"],
}
ACKNOWLEDGEMENT = phrases(DEFAULT_LANGUAGE).acknowledgement
SYSTEM = "Speak in short sentences and never invent a slot."
TWO_STAGE = {
    "entry": "identify",
    "system": SYSTEM,
    "nodes": [IDENTIFY, BOOK],
    "edges": [{"from": "identify", "to": "book", "requires": ["patient_id"]}],
}


def two_stage_state() -> CallGraph:
    return CallGraph.start(parse_graph(TWO_STAGE))


class FakeClient:
    """Replays a scripted sequence of model responses, one per completion."""

    default_model = "fake"

    def __init__(self, *completions):
        self.completions = list(completions)
        self.offered: list[list[str]] = []
        self.prompts: list[str] = []

    def complete_with_tools(self, prompt, tool_definitions, **_):
        self.prompts.append(prompt)
        self.offered.append([tool["function"]["name"] for tool in tool_definitions])
        return self.completions.pop(0)

    def complete_structured(self, prompt, _schema, **_):
        self.prompts.append(prompt)
        from agent.llm import StructuredCompletion
        from agent.models import AgentResponse

        answer = "   " if getattr(self, "blank_final", False) else "All set."
        return StructuredCompletion(
            data=AgentResponse(immediate_answer=answer), usage=Usage()
        )


def says(text, *calls):
    return ToolCompletion(
        text=text,
        tool_calls=[LLMToolCall(call_id=f"c{i}", name=n, arguments=a) for i, (n, a) in enumerate(calls)],
        usage=Usage(),
    )


class FakeRepository:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self.submissions: list[str] = []

    async def seed_default_guardrails(self):
        return None

    async def list_guardrails(self):
        return []

    async def append_event(self, _call_id, event_type, payload):
        self.events.append((event_type, payload))

    async def record_submission(self, _call_id, action, *_args):
        self.submissions.append(action)


def run_turn(prompt, state, client, repository, tool_outputs=None):
    """Drive one turn with the clinic tools stubbed out."""
    import agent.agent as module

    outputs = tool_outputs or {}
    original_api = module.ClinicApi.from_environment
    original_tools = module.create_clinic_tools

    class StubApi:
        def close(self):
            pass

    def stub_tools(_api, _call_id):
        def make(name):
            def call(**kwargs):
                result = outputs.get(name, {"ok": True})
                if callable(result):
                    return result(**kwargs)
                if isinstance(result, Exception):
                    raise result
                return result

            call.__name__ = name
            return call

        return [make(name) for name in ("search_patients", "book_appointment", "search_availability")]

    module.ClinicApi.from_environment = staticmethod(lambda: StubApi())
    module.create_clinic_tools = stub_tools
    try:
        async def drive():
            return [r.immediate_answer async for r in run_agent_turn(
                prompt, "CA-1", repository, client, state=state
            )]

        return asyncio.run(drive())
    finally:
        module.ClinicApi.from_environment = original_api
        module.create_clinic_tools = original_tools


# --- graph validation -------------------------------------------------------

def test_edge_to_a_missing_stage_is_rejected():
    with pytest.raises(InvalidGraph):
        parse_graph({
            "entry": "identify",
            "nodes": [IDENTIFY],
            "edges": [{"from": "identify", "to": "nowhere", "requires": []}],
        })


def test_unknown_tool_name_is_rejected():
    with pytest.raises(InvalidGraph):
        parse_graph({
            "entry": "a",
            "nodes": [{"id": "a", "tools": ["teleport"]}],
        })


def test_entry_must_exist():
    with pytest.raises(InvalidGraph):
        parse_graph({"entry": "missing", "nodes": [IDENTIFY]})


def test_duplicate_stage_ids_are_rejected():
    with pytest.raises(InvalidGraph):
        parse_graph({"entry": "identify", "nodes": [IDENTIFY, IDENTIFY]})


# --- eligibility and facts --------------------------------------------------

def test_transition_is_blocked_until_its_facts_exist():
    state = two_stage_state()
    assert state.refuse_reason("go_to", {"stage": "book"})
    state.facts["patient_id"] = "P1"
    assert state.refuse_reason("go_to", {"stage": "book"}) == ""


def test_entering_a_stage_clears_the_facts_it_resets():
    state = two_stage_state()
    state.facts.update({"patient_id": "P1", "slot": "10:00"})
    assert state.enter("book") == ["slot"]
    assert state.facts == {"patient_id": "P1"}


def test_record_facts_rejects_a_nested_payload():
    state = two_stage_state()
    assert state.refuse_reason("record_facts", {"facts": {"a": {"b": 1}}})
    assert state.refuse_reason("record_facts", {"facts": {"a": "b"}}) == ""


def test_every_model_call_records_its_time_and_prompt_size(monkeypatch):
    """Tool latency was already visible; without this the model's never was."""
    import agent.agent as module

    recorded = []
    monkeypatch.setattr(
        module,
        "emit",
        lambda call_id, kind, payload=None: recorded.append((call_id, kind, payload)),
    )

    state = two_stage_state()
    client = FakeClient(
        says("One moment.", ("search_patients", {"name": "Ana"})),
        says("", ("record_facts", {"facts": {"patient_id": "P1"}})),
        says("", ("record_facts", {"facts": {"verified": "yes"}})),
        says("", ("record_facts", {"facts": {"ready": "yes"}})),
    )

    run_turn("I'm Ana", state, client, FakeRepository(),
             {"search_patients": {"matches": [{"patient_id": "P1"}]}})

    model_calls = [p for _, kind, p in recorded if kind == module.LLM_EVENT]
    # Four silent/tool steps exhaust the budget, followed by the closing answer.
    assert [call["purpose"] for call in model_calls] == ["tools"] * 4 + ["answer"]
    # A number nobody can trace back to a call is not observability.
    assert {cid for cid, kind, _ in recorded if kind == module.LLM_EVENT} == {"CA-1"}
    assert [call["stage"] for call in model_calls] == [
        "identify",
        "identify",
        "book",
        "book",
        "book",
    ]
    assert all(call["turn"] == 1 for call in model_calls)
    # The size must be the size of the prompt that was actually sent, not merely
    # some growing number: compare against what the client received.
    assert [call["prompt_chars"] for call in model_calls] == [
        len(prompt) for prompt in client.prompts
    ]

    turn = next(p for _, kind, p in recorded if kind == "turn_finished")
    assert turn["ms"] >= 0


def test_the_running_graph_supplies_the_system_prompt():
    """The prompt is configuration the builder saves, not a file sitting beside the code."""
    state = two_stage_state()
    client = FakeClient(says("Who is calling?"))

    run_turn("hello", state, client, FakeRepository())

    assert SYSTEM in client.prompts[0]


def test_prompt_names_the_exact_keys_a_transition_needs():
    from agent.stage_runtime import render_context

    context = render_context(two_stage_state())
    assert "patient_id" in context
    assert "book" in context


def test_repeated_catalogue_is_rendered_once_without_changing_history():
    from copy import deepcopy

    from agent.stage_runtime import HistoryEntry, Operation, render_context

    catalogue = {"locations": [{"id": "sur", "name": "Arenal Sur"}]}
    state = two_stage_state()
    state.history = [
        HistoryEntry("agent", operations=[
            Operation("get_clinic_catalogue", {}, "executed", result=catalogue)
        ]),
        HistoryEntry("caller", text="Which clinics see children?"),
        HistoryEntry("agent", operations=[
            Operation("get_clinic_catalogue", {}, "executed", result=deepcopy(catalogue))
        ]),
    ]
    original_history = deepcopy(state.history)

    context = render_context(state)

    assert context.count(str(catalogue)) == 1
    assert "get_clinic_catalogue({}) -> same result as catalogue #1 above" in context
    assert "Which clinics see children?" in context
    assert state.history == original_history


def test_catalogue_deduplication_preserves_changes_errors_and_availability():
    from agent.stage_runtime import HistoryEntry, Operation, render_context

    first = {"clinic_name": "Before update"}
    updated = {"clinic_name": "After update"}
    slots = {"slots": [{"start_time": "2026-09-22T09:00:00+02:00"}]}
    state = two_stage_state()
    state.history = [HistoryEntry("agent", operations=[
        Operation("get_clinic_catalogue", {}, "failed", detail="temporary failure"),
        Operation("get_clinic_catalogue", {}, "executed", result=first),
        Operation("get_clinic_catalogue", {}, "executed", result=updated),
        Operation("get_clinic_catalogue", {}, "executed", result=first),
        Operation("search_availability", {"location_id": "sur"}, "executed", result=slots),
        Operation("search_availability", {"location_id": "norte"}, "executed", result=slots),
    ])]

    context = render_context(state)

    assert str(first) in context
    assert str(updated) in context
    assert "temporary failure" in context
    assert context.count(str(slots)) == 2
    assert context.count(str(first)) == 1
    assert f"{first} [catalogue #1]" in context
    assert f"{updated} [catalogue #2]" in context
    assert "same result as catalogue #1 above" in context


# --- the turn loop ----------------------------------------------------------

def test_internal_reply_is_replaced_before_speech_and_history(monkeypatch):
    import agent.agent as module

    events = []
    monkeypatch.setattr(module, "emit", lambda cid, kind, payload: events.append((kind, payload)))
    state = two_stage_state()
    leaked = "Final outcome already recorded: submit_no_action. Wait! The instruction explicitly states..."

    spoken = run_turn("No, I meant my daughter", state, FakeClient(says(leaked)), FakeRepository())

    assert spoken == [phrases(DEFAULT_LANGUAGE).error]
    assert all(leaked not in entry.text for entry in state.history)
    assert any(kind == "speech_blocked" for kind, _ in events)


def test_structured_final_answer_also_blocks_internal_text():
    from agent.llm import StructuredCompletion
    from agent.models import AgentResponse

    class LeakingFinalClient(FakeClient):
        def complete_structured(self, prompt, _schema, **_):
            return StructuredCompletion(
                data=AgentResponse(immediate_answer="I must call record_facts before speaking."),
                usage=Usage(),
            )

    client = LeakingFinalClient(*[
        says("", ("record_facts", {"facts": {f"step{i}": "done"}})) for i in range(4)
    ])
    state = two_stage_state()

    spoken = run_turn("Hello", state, client, FakeRepository())

    assert spoken == [phrases(DEFAULT_LANGUAGE).error]
    assert all("I must call" not in entry.text for entry in state.history)


def test_a_forbidden_tool_is_refused_and_the_promise_is_never_spoken():
    state = two_stage_state()
    repository = FakeRepository()
    client = FakeClient(
        says("I'll book that right now.", ("book_appointment", {"patient_id": "P1"})),
        says("Who am I speaking with?"),
    )

    spoken = run_turn("Book me in", state, client, repository)

    assert "book that right now" not in " ".join(spoken)
    assert spoken == ["Who am I speaking with?"]
    assert repository.submissions == []
    assert "book_appointment" not in client.offered[0]


def test_one_refused_call_suppresses_the_whole_utterance():
    state = two_stage_state()
    client = FakeClient(
        says(
            "Noted, booking now.",
            ("record_facts", {"facts": {"name": "Lucas"}}),
            ("book_appointment", {"patient_id": "P1"}),
        ),
        says("What is your ID?"),
    )

    spoken = run_turn("hi", state, client, FakeRepository())

    assert "booking now" not in " ".join(spoken)
    # Only the promise is withheld; the harmless fact write still lands, so the
    # model does not have to ask for the name again on its next attempt.
    assert state.facts == {"name": "Lucas"}


def test_a_facts_only_step_continues_to_the_callers_question():
    state = two_stage_state()
    client = FakeClient(
        says("What day suits you?", ("record_facts", {"facts": {"specialty": "cardio"}})),
        says("What day suits you?"),
    )

    spoken = run_turn("I need a cardiologist", state, client, FakeRepository())

    assert spoken == ["What day suits you?"]
    assert state.facts == {"specialty": "cardio"}


def test_bookkeeping_filler_does_not_delay_an_accepted_booking():
    state = two_stage_state()
    repository = FakeRepository()
    client = FakeClient(
        says("Certainly.", ("record_facts", {"facts": {"patient_id": "P1"}})),
        says("", ("go_to", {"stage": "book"})),
        says("Booked.", ("book_appointment", {"patient_id": "P1"})),
        says("Your booking is confirmed."),
    )

    spoken = run_turn("Yes, please book it.", state, client, repository)

    assert repository.submissions == ["BOOK"]
    assert spoken == [ACKNOWLEDGEMENT, "Your booking is confirmed."]


def test_a_tool_result_continues_the_turn_without_the_caller():
    state = two_stage_state()
    client = FakeClient(
        says("One moment.", ("search_patients", {"name": "Lucas"})),
        says("", ("record_facts", {"facts": {"patient_id": "P1"}}), ("go_to", {"stage": "book"})),
        says("You are in the system, when suits you?"),
    )

    spoken = run_turn("I'm Lucas", state, client, FakeRepository(),
                      {"search_patients": {"patients": [{"id": "P1"}]}})

    assert state.stage_id == "book"
    assert state.facts["patient_id"] == "P1"
    assert spoken == [ACKNOWLEDGEMENT, "You are in the system, when suits you?"]


def test_bookkeeping_steps_stay_silent():
    state = two_stage_state()
    state.facts["patient_id"] = "P1"
    client = FakeClient(
        says("", ("go_to", {"stage": "book"})),
        says("When would you like to come in?"),
    )

    spoken = run_turn("book me", state, client, FakeRepository())

    assert spoken == ["When would you like to come in?"]


def test_the_budget_always_ends_with_a_spoken_answer():
    state = two_stage_state()
    client = FakeClient(*[
        says(f"step {i}", ("search_patients", {"name": "Lucas"})) for i in range(4)
    ])

    spoken = run_turn("hello", state, client, FakeRepository())

    assert spoken[-1] == "All set."
    assert len(client.offered) == 4


def test_a_failing_tool_becomes_feedback_not_a_dead_turn():
    state = two_stage_state()
    client = FakeClient(
        says("Checking.", ("search_patients", {"name": "Lucas"})),
        says("Sorry, I could not look that up."),
    )

    spoken = run_turn("I'm Lucas", state, client, FakeRepository(),
                      {"search_patients": RuntimeError("upstream down")})

    assert spoken == [ACKNOWLEDGEMENT, "Sorry, I could not look that up."]


# --- the builder's HTTP surface ---------------------------------------------

def test_graph_api_round_trips_and_refuses_a_broken_graph(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import agent.graph as graph_module
    import agent.graph_api as api_module

    path = tmp_path / "graph.json"
    monkeypatch.setattr(graph_module, "GRAPH_PATH", path)
    graph_module.save_graph(parse_graph(TWO_STAGE), path)
    monkeypatch.setattr(api_module, "load_graph", lambda: graph_module.load_graph(path))
    monkeypatch.setattr(api_module, "save_graph", lambda graph: graph_module.save_graph(graph, path))

    app = FastAPI()
    api_module.register_graph_api(app)
    client = TestClient(app)

    assert client.get("/api/graph").json()["entry"] == "identify"
    assert len(client.get("/api/tools").json()["tools"]) == 10

    broken = {"entry": "identify", "nodes": [IDENTIFY], "edges": [{"from": "identify", "to": "gone"}]}
    assert client.put("/api/graph", json=broken).status_code == 400
    # The rejected save must not have touched the file on disk.
    assert graph_module.load_graph(path).entry == "identify"

    renamed = {**TWO_STAGE, "nodes": [{**IDENTIFY, "prompt": "Ask for their ID."}, BOOK]}
    assert client.put("/api/graph", json=renamed).status_code == 200
    assert graph_module.load_graph(path).node("identify").prompt == "Ask for their ID."

    # Editing the system prompt in the builder is the point of keeping it on the graph.
    assert client.get("/api/graph").json()["system"] == SYSTEM
    reworded = {**TWO_STAGE, "system": "Answer in Catalan."}
    assert client.put("/api/graph", json=reworded).status_code == 200
    assert graph_module.load_graph(path).system == "Answer in Catalan."


# --- failure handling -------------------------------------------------------

def test_a_failed_submission_is_not_retried_in_the_same_turn():
    """A timeout after the clinic accepted the request must not book twice."""
    state = two_stage_state()
    state.facts["patient_id"] = "P1"
    state.enter("book")
    repository = FakeRepository()
    client = FakeClient(
        says("Booking that.", ("book_appointment", {"patient_id": "P1", "slot": "09:00"})),
        says("Trying another slot.", ("book_appointment", {"patient_id": "P1", "slot": "10:00"})),
        says("I could not confirm that booking."),
    )

    spoken = run_turn("book 9am", state, client, repository,
                      {"book_appointment": RuntimeError("Response timed out after request acceptance")})

    assert repository.submissions == []
    assert spoken[-1] == "I could not confirm that booking."
    # The second attempt was refused, so its promise was never spoken either.
    assert "Trying another slot." not in spoken


def test_provider_wording_never_reaches_the_next_prompt():
    state = two_stage_state()
    client = FakeClient(
        says("Checking.", ("search_patients", {"name": "Ana"})),
        says("I could not look that up."),
    )

    run_turn("hi", state, client, FakeRepository(),
             {"search_patients": RuntimeError("secret clinic details")})

    assert "secret clinic details" not in client.prompts[-1]


def test_silent_facts_continue_to_a_transition_without_an_extra_caller_turn():
    """Saving the identified patient must not force a redundant question."""
    state = two_stage_state()
    client = FakeClient(
        says("One moment.", ("search_patients", {"name": "Ana"})),
        says("", ("record_facts", {"facts": {"patient_id": "P1"}})),
        says("", ("go_to", {"stage": "book"})),
        says("When would you like to come in?"),
    )

    spoken = run_turn("I'm Ana", state, client, FakeRepository(),
                      {"search_patients": {"patients": [{"id": "P1"}]}})

    assert spoken == [ACKNOWLEDGEMENT, "When would you like to come in?"]
    assert state.stage_id == "book"
    assert len(client.offered) == 4


def test_an_identical_call_is_not_repeated_within_a_turn():
    """A model that keeps asking the same question would spend the whole budget."""
    state = two_stage_state()
    client = FakeClient(*[
        says("Un momento.", ("search_patients", {"name": "Ana"})) for _ in range(4)
    ])

    spoken = run_turn("Soy Ana", state, client, FakeRepository(),
                      {"search_patients": {"patients": []}})

    assert spoken == [ACKNOWLEDGEMENT, "All set."]


def test_a_second_booking_in_the_same_batch_is_refused_after_the_first_fails():
    """Planning judged the batch before any of it ran; the guard must run again."""
    state = two_stage_state()
    state.facts["patient_id"] = "P1"
    state.enter("book")
    repository = FakeRepository()
    client = FakeClient(
        says("Booking that.",
             ("book_appointment", {"patient_id": "P1", "slot": "09:00"}),
             ("book_appointment", {"patient_id": "P1", "slot": "10:00"})),
        says("I could not confirm the booking."),
    )

    attempts = []

    def timing_out(**kwargs):
        attempts.append(kwargs)
        raise RuntimeError("Response timed out after request acceptance")

    run_turn("book me in", state, client, repository, {"book_appointment": timing_out})

    assert len(attempts) == 1
    assert repository.submissions == []


def test_a_failed_lookup_may_be_tried_again_but_a_failed_submission_may_not():
    state = two_stage_state()
    state.failed_tools.add("search_patients")
    state.failed_tools.add("book_appointment")

    assert state.refuse_reason("search_patients", {"name": "Ana"}) == ""
    state.enter("book")
    assert state.refuse_reason("book_appointment", {"patient_id": "P1"})
    state.start_turn()
    assert state.refuse_reason("book_appointment", {"patient_id": "P1"})
    assert state.refuse_reason("search_availability", {}) == ""


def test_a_failed_call_is_not_remembered_as_already_answered():
    state = two_stage_state()
    client = FakeClient(
        says("Checking.", ("search_patients", {"name": "Ana"})),
        says("Trying again.", ("search_patients", {"name": "Ana"})),
        says("Found you."),
    )

    calls = []

    def flaky(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("timeout")
        return {"patients": [{"id": "P1"}]}

    spoken = run_turn("Soy Ana", state, client, FakeRepository(), {"search_patients": flaky})

    assert len(calls) == 2
    assert spoken[-1] == "Found you."


def test_a_blank_final_answer_still_says_something():
    from agent.language import DEFAULT_LANGUAGE, phrases

    state = two_stage_state()
    client = FakeClient(
        says("Checking.", ("search_patients", {"name": "Ana"})),
        says(""),
    )
    client.blank_final = True

    spoken = run_turn("Soy Ana", state, client, FakeRepository(),
                      {"search_patients": {"patients": [{"id": "P1"}]}})

    assert spoken[-1] == phrases(DEFAULT_LANGUAGE).no_answer


def test_a_tool_request_never_speaks_success_before_it_runs():
    state = two_stage_state()
    state.enter("book")
    client = FakeClient(
        says("Your appointment is booked.", ("book_appointment", {"patient_id": "P1"})),
        says("I could not confirm the booking."),
    )

    spoken = run_turn("yes", state, client, FakeRepository(),
                      {"book_appointment": RuntimeError("timeout")})

    assert spoken == [ACKNOWLEDGEMENT, "I could not confirm the booking."]


def test_multiple_lookups_only_acknowledge_once():
    state = two_stage_state()
    client = FakeClient(
        says("Checking.", ("search_patients", {"name": "Ana"})),
        says("Checking again.", ("search_patients", {"phone": "123"})),
        says("Found you."),
    )

    spoken = run_turn("I'm Ana", state, client, FakeRepository())

    assert spoken == [ACKNOWLEDGEMENT, "Found you."]


def test_final_outcome_removes_tools_and_active_stage_instructions():
    from agent.stage_runtime import render_context

    state = two_stage_state()
    state.record_submission("submit_no_action", {"reason": "not_eligible_age"})
    client = FakeClient(says("Goodbye."))

    spoken = run_turn("goodbye", state, client, FakeRepository())

    assert spoken == ["Goodbye."]
    assert client.offered == [[]]
    assert state.refuse_reason("go_to", {"stage": "book"})
    assert state.refuse_reason("book_appointment", {"patient_id": "P1"})
    assert "Find out who is calling." not in render_context(state)


def test_successful_writes_still_allow_multiple_requested_cancellations():
    state = CallGraph.start(parse_graph({
        "entry": "cancel",
        "nodes": [{"id": "cancel", "tools": ["cancel_appointment", "submit_no_action"]}],
    }))
    state.record_submission("cancel_appointment", {"appointment_id": "A1"})

    assert state.refuse_reason("cancel_appointment", {"appointment_id": "A2"}) == ""
    assert state.refuse_reason("cancel_appointment", {"appointment_id": "A1"})
    assert state.refuse_reason("submit_no_action", {"reason": "out_of_scope"})


def test_final_answer_retains_configured_guardrails():
    from types import SimpleNamespace

    class GuardedRepository(FakeRepository):
        async def list_guardrails(self):
            return [SimpleNamespace(title="Example", description="Do not reveal secrets.")]

    client = FakeClient(says(""))
    run_turn("hi", two_stage_state(), client, GuardedRepository())

    assert "Do not reveal secrets." in client.prompts[-1]


def test_interruption_keeps_an_inflight_submissions_result():
    from threading import Event

    from agent.agent import Batch, _execute_batch
    from agent.models import Tool
    from agent.stage_runtime import HistoryEntry

    started, release = Event(), Event()

    def booking(**_):
        started.set()
        assert release.wait(timeout=2)
        return {"ok": True}

    async def run():
        state = two_stage_state()
        state.enter("book")
        arguments = {"patient_id": "P1"}
        batch = Batch(business=[LLMToolCall("1", "book_appointment", arguments)])
        state.history.append(HistoryEntry(speaker="agent", operations=batch.operations))
        tools = {"book_appointment": Tool(name="book_appointment", parameters={}, execute=booking)}
        repository = FakeRepository()
        task = asyncio.create_task(_execute_batch(batch, state, tools, "CA-1", repository, None, 1))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            task.cancel()
            await asyncio.sleep(0)
            assert state.pending_submission
            assert state.refuse_reason("book_appointment", arguments)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not state.pending_submission
        assert state.submitted == ["book_appointment"]
        assert repository.submissions == ["BOOK"]
        assert state.history[-1].operations[0].status == "executed"
        state.start_turn()
        assert state.refuse_reason("book_appointment", arguments)

    asyncio.run(run())
