import asyncio
from datetime import datetime

import pytest

from observability import CallUpdate, Event, EventBus, Store
from observability.store import CLINIC_TIMEZONE


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "calls.db")
    yield store
    store.close()


async def drain(bus: EventBus) -> None:
    """Run the bus until its queue is empty, then stop."""
    task = asyncio.create_task(bus.run())
    await asyncio.sleep(0)
    while not bus._queue.empty():
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()


def test_partials_are_broadcast_but_not_persisted(store):
    store.write(
        [
            Event("CA1", 1.0, "stt_partial", {"text": "quería ci"}),
            Event("CA1", 2.0, "stt_final", {"text": "quería cita"}),
        ]
    )
    kinds = [event["kind"] for event in store.get_events("CA1")]
    assert kinds == ["stt_final"]


def test_update_call_upserts_and_keeps_earlier_fields(store):
    store.write([CallUpdate("CA1", {"started_at": 10.0, "from_number": "+34600"})])
    store.write([CallUpdate("CA1", {"outcome": "BOOK", "ended_at": 42.0})])

    call = store.get_call("CA1")
    assert call["from_number"] == "+34600"
    assert call["outcome"] == "BOOK"
    # Numeric columns must come back as numbers, not strings, or the dashboard
    # cannot draw a duration ring.
    assert call["ended_at"] == 42.0


def test_unknown_fields_never_reach_sql(store):
    store.write([CallUpdate("CA1", {"started_at": 1.0, "; DROP TABLE calls": "x"})])
    assert store.get_call("CA1") is not None


def test_live_calls_sort_first(store):
    store.write([CallUpdate("OLD", {"started_at": 100.0, "ended_at": 200.0})])
    store.write([CallUpdate("LIVE", {"started_at": 1.0})])
    assert [call["call_id"] for call in store.list_calls()] == ["LIVE", "OLD"]


def test_orphans_from_a_dead_process_are_closed(tmp_path):
    path = tmp_path / "calls.db"
    first = Store(path)
    first.write([CallUpdate("CA1", {"started_at": 5.0})])
    first.close()

    second = Store(path)
    assert second.get_call("CA1")["ended_at"] == 5.0
    second.close()


def test_bus_delivers_to_store_and_subscribers(store):
    async def scenario():
        bus = EventBus(store)
        with bus.subscribe() as queue:
            bus.update_call("CA1", started_at=1.0)
            bus.emit("CA1", "stt_final", {"text": "hola"})
            await drain(bus)
            delivered = [queue.get_nowait() for _ in range(queue.qsize())]

        assert [event["kind"] for event in store.get_events("CA1")] == ["stt_final"]
        # The partial-skipping rule is the store's, not the bus's: a dashboard
        # sees everything.
        assert len(delivered) == 2

    asyncio.run(scenario())


def test_a_full_queue_drops_instead_of_blocking(store):
    async def scenario():
        bus = EventBus(store)
        bus._queue = asyncio.Queue(maxsize=2)
        for index in range(50):
            bus.emit("CA1", "stt_partial", {"text": str(index)})
        assert bus.dropped == 48

    asyncio.run(scenario())


def test_closing_a_call_does_not_overwrite_its_start(store):
    store.write([CallUpdate("CA1", {"started_at": 100.0, "state": "connected"})])
    store.write([CallUpdate("CA1", {"ended_at": 160.0, "state": "ended"})])

    call = store.get_call("CA1")
    assert call["started_at"] == 100.0
    assert call["ended_at"] - call["started_at"] == 60.0


def test_history_filters_by_outcome_and_reason(store):
    store.write(
        [
            CallUpdate("A", {"started_at": 1.0, "ended_at": 2.0, "outcome": "BOOK"}),
            CallUpdate(
                "B",
                {
                    "started_at": 3.0,
                    "ended_at": 4.0,
                    "outcome": "NO_ACTION",
                    "reason": "no_availability",
                },
            ),
        ]
    )
    assert [c["call_id"] for c in store.list_calls(outcome="BOOK")] == ["A"]
    assert [c["call_id"] for c in store.list_calls(reason="no_availability")] == ["B"]
    assert len(store.list_calls()) == 2


def test_history_search_reaches_into_the_transcript(store):
    store.write(
        [
            CallUpdate("A", {"started_at": 1.0, "ended_at": 2.0}),
            CallUpdate("B", {"started_at": 3.0, "ended_at": 4.0}),
            Event("A", 1.5, "stt_final", {"text": "quería cita con la dermatóloga"}),
            Event("B", 3.5, "stt_final", {"text": "quería cancelar la de mi hijo"}),
            # A partial is never stored, so it must never be findable either.
            Event("B", 3.6, "stt_partial", {"text": "dermatóloga"}),
        ]
    )
    assert [c["call_id"] for c in store.list_calls(search="dermatóloga")] == ["A"]
    assert [c["call_id"] for c in store.list_calls(search="cancelar")] == ["B"]


def test_stats_summarise_the_history(store):
    store.write(
        [
            CallUpdate(
                "A",
                {
                    "started_at": 1.0,
                    "ended_at": 2.0,
                    "outcome": "BOOK",
                    "ttfa_seconds": 0.4,
                    "cost_eur": 0.02,
                },
            ),
            CallUpdate(
                "B",
                {
                    "started_at": 3.0,
                    "ended_at": 4.0,
                    "outcome": "BOOK",
                    "ttfa_seconds": 0.8,
                    "cost_eur": 0.04,
                },
            ),
            CallUpdate("C", {"started_at": 5.0, "ended_at": 6.0, "error": "boom"}),
        ]
    )
    stats = store.stats()
    assert stats["calls"] == 3
    assert stats["failed"] == 1
    assert stats["ttfa_p50"] == 0.4
    assert stats["ttfa_p95"] == 0.8
    assert {o["outcome"]: o["count"] for o in stats["outcomes"]} == {
        "BOOK": 2,
        "sin registrar": 1,
    }


def test_paging_walks_the_history(store):
    store.write(
        [
            CallUpdate(f"C{i}", {"started_at": float(i), "ended_at": float(i) + 1})
            for i in range(5)
        ]
    )
    first = store.list_calls(limit=2)
    second = store.list_calls(limit=2, offset=2)
    assert len(first) == len(second) == 2
    assert not {c["call_id"] for c in first} & {c["call_id"] for c in second}


def test_history_excludes_calls_still_in_flight(store):
    store.write(
        [
            CallUpdate("LIVE", {"started_at": 1.0}),
            CallUpdate("DONE", {"started_at": 2.0, "ended_at": 3.0}),
        ]
    )
    assert [c["call_id"] for c in store.list_calls(ended_only=True)] == ["DONE"]
    assert len(store.list_calls()) == 2


def test_history_filters_by_patient_and_insurer(store):
    store.write(
        [
            CallUpdate(
                "A",
                {
                    "started_at": 1.0,
                    "ended_at": 2.0,
                    "patient_name": "Marta Ruiz Gómez",
                    "insurer": "sanitas",
                },
            ),
            CallUpdate(
                "B",
                {
                    "started_at": 3.0,
                    "ended_at": 4.0,
                    "patient_name": "Álvaro Cid",
                    "insurer": "adeslas",
                },
            ),
        ]
    )
    assert [c["call_id"] for c in store.list_calls(name="ruiz")] == ["A"]
    assert [c["call_id"] for c in store.list_calls(name="Gómez")] == ["A"]
    assert [c["call_id"] for c in store.list_calls(insurer="adeslas")] == ["B"]


def test_history_filters_by_day_in_clinic_time(store):
    # 2026-09-19 00:30 and 23:30 Europe/Madrid, either side of a naive UTC day.
    early = datetime(2026, 9, 19, 0, 30, tzinfo=CLINIC_TIMEZONE).timestamp()
    late = datetime(2026, 9, 19, 23, 30, tzinfo=CLINIC_TIMEZONE).timestamp()
    before = datetime(2026, 9, 18, 23, 30, tzinfo=CLINIC_TIMEZONE).timestamp()
    store.write(
        [
            CallUpdate("EARLY", {"started_at": early, "ended_at": early + 1}),
            CallUpdate("LATE", {"started_at": late, "ended_at": late + 1}),
            CallUpdate("BEFORE", {"started_at": before, "ended_at": before + 1}),
        ]
    )
    same_day = store.list_calls(date_from="2026-09-19", date_to="2026-09-19")
    # date_to is inclusive of its whole day, so a call at 23:30 still counts.
    assert sorted(c["call_id"] for c in same_day) == ["EARLY", "LATE"]
    assert len(store.list_calls(date_from="2026-09-18")) == 3


def test_histogram_counts_a_reasoned_close_as_a_record(store):
    # NO_ACTION with a reason scores like a booking; only an empty record
    # always fails. Colouring them alike would misread a passing run.
    base = datetime(2026, 9, 19, 10, 0, tzinfo=CLINIC_TIMEZONE).timestamp()
    store.write(
        [
            CallUpdate(
                "A", {"started_at": base, "ended_at": base + 1, "outcome": "BOOK"}
            ),
            CallUpdate(
                "B",
                {
                    "started_at": base + 60,
                    "ended_at": base + 61,
                    "outcome": "NO_ACTION",
                    "reason": "no_availability",
                },
            ),
            CallUpdate(
                "C",
                {
                    "started_at": base + 120,
                    "ended_at": base + 121,
                    "outcome": "ESCALATE",
                    "reason": "medical_emergency",
                },
            ),
            CallUpdate("D", {"started_at": base + 180, "ended_at": base + 181}),
        ]
    )
    totals = store.histogram()["buckets"]
    assert sum(b["wrote"] for b in totals) == 1
    assert sum(b["closed"] for b in totals) == 2
    assert sum(b["absent"] for b in totals) == 1


def test_histogram_buckets_widen_with_the_span(store):
    base = datetime(2026, 9, 19, 10, 0, tzinfo=CLINIC_TIMEZONE).timestamp()
    store.write(
        [
            CallUpdate(
                f"C{i}", {"started_at": base + i * 30, "ended_at": base + i * 30 + 1}
            )
            for i in range(10)
        ]
    )
    assert store.histogram()["bucket_seconds"] == 60

    store.write(
        [CallUpdate("FAR", {"started_at": base + 40 * 3600, "ended_at": base + 1})]
    )
    # Forty hours cannot be drawn in one-minute bars, so the ladder steps up.
    assert store.histogram()["bucket_seconds"] > 60


def test_histogram_honours_the_same_filters_as_the_table(store):
    base = datetime(2026, 9, 19, 10, 0, tzinfo=CLINIC_TIMEZONE).timestamp()
    store.write(
        [
            CallUpdate(
                "A",
                {
                    "started_at": base,
                    "ended_at": base + 1,
                    "outcome": "BOOK",
                    "insurer": "sanitas",
                },
            ),
            CallUpdate(
                "B",
                {
                    "started_at": base + 60,
                    "ended_at": base + 61,
                    "outcome": "BOOK",
                    "insurer": "adeslas",
                },
            ),
        ]
    )
    only = store.histogram(insurer="sanitas")["buckets"]
    assert sum(b["wrote"] for b in only) == 1
