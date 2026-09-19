import asyncio
import json

import httpx
import pytest

import scoring.jev
from observability import CallUpdate, Event
from scoring import QUESTIONS, build_state, run_scoring_worker, score_conversation
from scoring.jev import JEV_URL


def make_events():
    return [
        {"kind": "stt_partial", "payload": {"text": "quería ci"}},
        {"kind": "stt_final", "payload": {"text": "quería cita"}},
        {"kind": "llm", "payload": {"text": "voy a buscar"}},
        {"kind": "tts", "payload": {"text": "¿Con qué especialista?"}},
        {"kind": "tool_result", "payload": {"name": "book"}},
        {"kind": "tts", "payload": {"text": "   "}},
        {"kind": "stt_final", "payload": {}},
        {"kind": "stt_final", "payload": {"text": "con la dermatóloga"}},
    ]


def make_call():
    return {"outcome": "BOOK", "reason": None}


def test_state_keeps_only_spoken_turns_in_order():
    state = build_state(make_call(), make_events())
    assert state["turns"] == [
        {"speaker": "patient", "text": "quería cita"},
        {"speaker": "agent", "text": "¿Con qué especialista?"},
        {"speaker": "patient", "text": "con la dermatóloga"},
    ]
    assert state["outcome"] == "BOOK"
    assert state["reason"] is None


def jev_client(handler, **_kwargs):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_score_conversation_posts_fixed_questions_and_reads_answers():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["Authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "jev-2026-09",
                "answers": {
                    key: {
                        "score": score,
                        "confidence": 0.9,
                        "probabilities": [0.1, 0.2, 0.3, 0.2, 0.2],
                    }
                    for key, score in zip(QUESTIONS, [4, 3, 2, 1, 0], strict=True)
                },
            },
        )

    async def scenario():
        async with jev_client(handler) as client:
            return await score_conversation(
                make_call(), make_events(), client, "test-key"
            )

    result = asyncio.run(scenario())

    assert captured["url"] == JEV_URL
    assert captured["authorization"] == "Bearer test-key"
    assert captured["body"]["model"] == "jev-latest"
    assert captured["body"]["questions"] == QUESTIONS
    assert captured["body"]["state"] == build_state(make_call(), make_events())

    assert result["overall"] == 50.0
    assert result["raw_overall"] == 50.0
    assert result["penalty"] == {"points": 0, "reasons": []}
    assert result["model"] == "jev-2026-09"
    assert result["scored_at"] > 0
    assert result["final"] is True
    assert result["turn_count"] == 1
    assert list(result["dimensions"]) == list(QUESTIONS)
    assert result["dimensions"]["resolution"]["score"] == 4
    assert result["dimensions"]["resolution"]["confidence"] == 0.9
    assert result["dimensions"]["language"]["score"] == 0


def score_call(call, scores, final=True):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answers": {
                    key: {"score": score}
                    for key, score in zip(QUESTIONS, scores, strict=True)
                }
            },
        )

    async def scenario():
        async with jev_client(handler) as client:
            return await score_conversation(
                call, make_events(), client, "k", final=final
            )

    return asyncio.run(scenario())


HALF = [4, 3, 2, 1, 0]


def test_final_call_without_action_loses_40_points():
    result = score_call({"started_at": 0.0, "ended_at": 40.0}, HALF)
    assert result["raw_overall"] == 50.0
    assert result["overall"] == 10.0
    assert result["penalty"] == {"points": 40, "reasons": ["no_action"]}


def test_early_hangup_is_an_audit_reason_not_a_second_deduction():
    result = score_call({"started_at": 0.0, "ended_at": 20.0}, HALF)
    assert result["overall"] == 10.0
    assert result["penalty"] == {
        "points": 40,
        "reasons": ["no_action", "early_hangup"],
    }


def test_penalty_clamps_overall_at_zero():
    result = score_call({"started_at": 0.0, "ended_at": 40.0}, [1] * 5)
    assert result["raw_overall"] == 25.0
    assert result["overall"] == 0.0
    assert result["penalty"]["points"] == 40


def test_live_score_without_action_is_never_penalized():
    result = score_call({"ended_at": None}, HALF, final=False)
    assert result["overall"] == 50.0
    assert result["penalty"] == {"points": 0, "reasons": []}


def test_no_action_outcome_is_not_penalized():
    result = score_call(
        {"started_at": 0.0, "ended_at": 20.0, "outcome": "NO_ACTION"}, HALF
    )
    assert result["overall"] == 50.0
    assert result["penalty"] == {"points": 0, "reasons": []}


def test_book_outcome_under_30_seconds_is_not_penalized():
    result = score_call(
        {"started_at": 0.0, "ended_at": 20.0, "outcome": "BOOK"}, HALF
    )
    assert result["overall"] == 50.0
    assert result["penalty"] == {"points": 0, "reasons": []}


def test_score_conversation_rejects_a_malformed_answer():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"answers": {"resolution": {"score": 9}}})

    async def scenario():
        async with jev_client(handler) as client:
            await score_conversation(make_call(), make_events(), client, "k")

    with pytest.raises(ValueError):
        asyncio.run(scenario())


def test_score_conversation_rejects_missing_answers():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async def scenario():
        async with jev_client(handler) as client:
            await score_conversation(make_call(), make_events(), client, "k")

    with pytest.raises(ValueError):
        asyncio.run(scenario())


class FakeStore:
    def __init__(self):
        self.calls = {}
        self.events = {}

    def get_call(self, call_id):
        return self.calls.get(call_id)

    def get_events(self, call_id):
        return self.events.get(call_id, [])


def collector(published):
    return lambda call_id, **fields: published.append((call_id, fields))


def run_worker(monkeypatch, handler, scenario):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        scoring.jev.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler)),
    )
    asyncio.run(scenario())


async def wait_until(predicate, tries=2000):
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not met")


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "jev-test",
            "answers": {key: {"score": 3} for key in QUESTIONS},
        },
    )


def tts(text):
    return Event("CA1", 1.0, "tts", {"text": text})


def test_tts_event_scores_live_and_publishes(monkeypatch):
    store = FakeStore()
    store.calls["CA1"] = {"outcome": "BOOK", "ended_at": None}
    store.events["CA1"] = [
        {"kind": "stt_final", "payload": {"text": "hola"}},
        {"kind": "tts", "payload": {"text": "buenas"}},
    ]
    published = []

    async def scenario():
        queue = asyncio.Queue()
        worker = asyncio.create_task(
            run_scoring_worker(store, queue, "k", publish=collector(published))
        )
        queue.put_nowait(tts("buenas"))
        await wait_until(lambda: published)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    run_worker(monkeypatch, ok_handler, scenario)

    call_id, fields = published[0][0], published[0][1]
    assert call_id == "CA1"
    assert fields["score_overall"] == 75.0
    assert fields["score_error"] is None
    detail = json.loads(fields["score_json"])
    assert detail["final"] is False
    assert detail["turn_count"] == 1


def test_non_tts_and_empty_tts_do_not_trigger(monkeypatch):
    store = FakeStore()
    store.calls["CA1"] = {"ended_at": None}
    store.events["CA1"] = [{"kind": "stt_final", "payload": {"text": "hola"}}]
    published = []
    requests = []

    async def handler(request):
        requests.append(request)
        return ok_handler(request)

    async def scenario():
        queue = asyncio.Queue()
        worker = asyncio.create_task(
            run_scoring_worker(store, queue, "k", publish=collector(published))
        )
        queue.put_nowait(Event("CA1", 1.0, "stt_final", {"text": "hola"}))
        queue.put_nowait(Event("CA1", 1.0, "tool_result", {"name": "book"}))
        queue.put_nowait(tts("   "))
        for _ in range(50):
            await asyncio.sleep(0)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    run_worker(monkeypatch, handler, scenario)
    assert not published
    assert not requests


def test_ended_callupdate_publishes_final(monkeypatch):
    store = FakeStore()
    store.calls["CA1"] = {"outcome": "BOOK", "ended_at": 2.0}
    store.events["CA1"] = [
        {"kind": "stt_final", "payload": {"text": "hola"}},
        {"kind": "tts", "payload": {"text": "buenas"}},
        {"kind": "tts", "payload": {"text": "adiós"}},
    ]
    published = []

    async def scenario():
        queue = asyncio.Queue()
        worker = asyncio.create_task(
            run_scoring_worker(store, queue, "k", publish=collector(published))
        )
        queue.put_nowait(CallUpdate("CA1", {"ended_at": 2.0}))
        await wait_until(lambda: published)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    run_worker(monkeypatch, ok_handler, scenario)

    detail = json.loads(published[0][1]["score_json"])
    assert detail["final"] is True
    assert detail["turn_count"] == 2


def test_late_tts_on_ended_call_stays_final(monkeypatch):
    store = FakeStore()
    store.calls["CA1"] = {"ended_at": 2.0, "outcome": "BOOK"}
    store.events["CA1"] = [{"kind": "tts", "payload": {"text": "buenas"}}]
    published = []

    async def scenario():
        queue = asyncio.Queue()
        worker = asyncio.create_task(
            run_scoring_worker(store, queue, "k", publish=collector(published))
        )
        queue.put_nowait(tts("buenas"))
        await wait_until(lambda: published)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    run_worker(monkeypatch, ok_handler, scenario)

    detail = json.loads(published[0][1]["score_json"])
    assert detail["final"] is True


def test_empty_transcript_publishes_error_without_http(monkeypatch):
    store = FakeStore()
    store.calls["CA1"] = {"ended_at": 2.0}
    store.events["CA1"] = []
    published = []
    requests = []

    async def handler(request):
        requests.append(request)
        return ok_handler(request)

    async def scenario():
        queue = asyncio.Queue()
        worker = asyncio.create_task(
            run_scoring_worker(store, queue, "k", publish=collector(published))
        )
        queue.put_nowait(CallUpdate("CA1", {"ended_at": 2.0}))
        await wait_until(lambda: published)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    run_worker(monkeypatch, handler, scenario)

    assert not requests
    fields = published[0][1]
    assert fields["score_overall"] is None
    assert fields["score_json"] is None
    assert fields["score_error"] == "no spoken turns to score"


def test_triggers_during_a_request_coalesce_into_one_rerun(monkeypatch):
    store = FakeStore()
    store.calls["CA1"] = {"ended_at": None}
    store.events["CA1"] = [
        {"kind": "stt_final", "payload": {"text": "hola"}},
        {"kind": "tts", "payload": {"text": "buenas"}},
    ]
    published = []
    requests = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def handler(request):
        requests.append(request)
        if len(requests) == 1:
            first_started.set()
            await release_first.wait()
        return ok_handler(request)

    async def scenario():
        queue = asyncio.Queue()
        worker = asyncio.create_task(
            run_scoring_worker(store, queue, "k", publish=collector(published))
        )
        queue.put_nowait(tts("buenas"))
        await wait_until(first_started.is_set)
        store.events["CA1"].append({"kind": "tts", "payload": {"text": "adiós"}})
        store.events["CA1"].append({"kind": "tts", "payload": {"text": "de verdad"}})
        queue.put_nowait(tts("adiós"))
        queue.put_nowait(tts("de verdad"))
        release_first.set()
        await wait_until(lambda: len(published) == 2)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    run_worker(monkeypatch, handler, scenario)
    assert len(requests) == 2
    turns = [
        turn["text"]
        for turn in json.loads(requests[1].content)["state"]["turns"]
    ]
    assert turns == ["hola", "buenas", "adiós", "de verdad"]


def test_calls_score_concurrently(monkeypatch):
    store = FakeStore()
    for call_id in ("A", "B"):
        store.calls[call_id] = {"ended_at": None}
        store.events[call_id] = [
            {"kind": "stt_final", "payload": {"text": "hola"}},
            {"kind": "tts", "payload": {"text": call_id}},
        ]
    published = []
    started = set()
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def handler(request):
        call_id = json.loads(request.content)["state"]["turns"][-1]["text"]
        started.add(call_id)
        if started == {"A", "B"}:
            both_started.set()
        await release.wait()
        return ok_handler(request)

    async def scenario():
        queue = asyncio.Queue()
        worker = asyncio.create_task(
            run_scoring_worker(store, queue, "k", publish=collector(published))
        )
        queue.put_nowait(Event("A", 1.0, "tts", {"text": "A"}))
        queue.put_nowait(Event("B", 1.0, "tts", {"text": "B"}))
        await wait_until(both_started.is_set)
        release.set()
        await wait_until(lambda: len(published) == 2)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    run_worker(monkeypatch, handler, scenario)
    assert {call_id for call_id, _ in published} == {"A", "B"}
