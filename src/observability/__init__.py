"""Call observability: the event stream behind the dashboard.

The agent layer only ever needs the three module-level functions. They are
sync, fire-and-forget and safe to call from anywhere, so nothing downstream
has to thread a bus through `AgentFactory` or `run_call`:

    from observability import emit, update_call

    emit(call_id, "tool_result", {"name": "search_patient", "response": body})
    update_call(call_id, patient_id="P00042", outcome="BOOK")
"""

from contextlib import contextmanager

from .bus import CallUpdate, Event, EventBus
from .store import Store

__all__ = [
    "CallUpdate",
    "Event",
    "EventBus",
    "Store",
    "emit",
    "get_bus",
    "set_bus",
    "subscribe",
    "update_call",
]

_bus: EventBus | None = None


def set_bus(bus: EventBus | None) -> None:
    global _bus
    _bus = bus


def get_bus() -> EventBus | None:
    return _bus


def emit(call_id: str, kind: str, payload: dict | None = None) -> None:
    # Observability is never a reason for a call to fail, so a server running
    # without a bus (tests, the smoke scripts) silently does nothing.
    if _bus is not None:
        _bus.emit(call_id, kind, payload)


def update_call(call_id: str, **fields) -> None:
    if _bus is not None:
        _bus.update_call(call_id, **fields)


@contextmanager
def subscribe():
    if _bus is None:
        raise RuntimeError("no event bus is running")
    with _bus.subscribe() as queue:
        yield queue
