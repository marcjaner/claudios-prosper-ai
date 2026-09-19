import asyncio

import pytest

from agent.agent import run_agent_turn
from agent.graph import InvalidGraph, parse_graph
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


# --- the turn loop ----------------------------------------------------------

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


def test_a_facts_only_step_ends_the_turn_so_the_caller_can_answer():
    state = two_stage_state()
    client = FakeClient(
        says("What day suits you?", ("record_facts", {"facts": {"specialty": "cardio"}})),
    )

    spoken = run_turn("I need a cardiologist", state, client, FakeRepository())

    assert spoken == ["What day suits you?"]
    assert state.facts == {"specialty": "cardio"}


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
    assert spoken == ["One moment.", "You are in the system, when suits you?"]


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

    assert spoken == ["Checking.", "Sorry, I could not look that up."]


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


def test_a_silent_step_still_answers_the_caller():
    """Ending a turn on a wordless bookkeeping step would leave dead air."""
    state = two_stage_state()
    client = FakeClient(
        says("One moment.", ("search_patients", {"name": "Ana"})),
        says("", ("record_facts", {"facts": {"patient_id": "P1"}})),
    )

    spoken = run_turn("I'm Ana", state, client, FakeRepository(),
                      {"search_patients": {"patients": [{"id": "P1"}]}})

    assert spoken == ["One moment.", "All set."]


def test_an_identical_call_is_not_repeated_within_a_turn():
    """A model that keeps asking the same question would spend the whole budget."""
    state = two_stage_state()
    client = FakeClient(*[
        says("Un momento.", ("search_patients", {"name": "Ana"})) for _ in range(4)
    ])

    spoken = run_turn("Soy Ana", state, client, FakeRepository(),
                      {"search_patients": {"patients": []}})

    assert spoken == ["Un momento.", "All set."]


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
        says("", ("record_facts", {"facts": {"patient_id": "P1"}})),
    )
    client.blank_final = True

    spoken = run_turn("Soy Ana", state, client, FakeRepository(),
                      {"search_patients": {"patients": [{"id": "P1"}]}})

    assert spoken[-1] == phrases(DEFAULT_LANGUAGE).no_answer
