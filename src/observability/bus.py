import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from loguru import logger

# The bus must never stall a call. Both queues drop rather than block: a wedged
# dashboard tab may not apply backpressure to twenty phone calls.
QUEUE_MAX = 20_000
SUBSCRIBER_MAX = 2_000
BATCH_MAX = 200


@dataclass(frozen=True, slots=True)
class Event:
    call_id: str
    ts: float
    kind: str
    payload: dict


@dataclass(frozen=True, slots=True)
class CallUpdate:
    call_id: str
    fields: dict


class EventBus:
    """Fans call events out to dashboards and into the store.

    `emit` and `update_call` are deliberately sync: a coroutine here would let
    someone add an await to the audio path by accident.
    """

    def __init__(self, store):
        self._store = store
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.dropped = 0

    def emit(self, call_id: str, kind: str, payload: dict | None = None) -> None:
        self._put(Event(call_id, time.time(), kind, payload or {}))

    def update_call(self, call_id: str, **fields) -> None:
        self._put(CallUpdate(call_id, fields))

    def _put(self, item: Event | CallUpdate) -> None:
        if self._loop is not None:
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if running_loop is not self._loop:
                self._loop.call_soon_threadsafe(self._put_nowait, item)
                return
        self._put_nowait(item)

    def _put_nowait(self, item: Event | CallUpdate) -> None:
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped += 1

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_MAX)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        while True:
            batch = [await self._queue.get()]
            while len(batch) < BATCH_MAX:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            # Dashboards first: a slow commit must not delay the live wall.
            self._broadcast(batch)
            try:
                self._store.write(batch)
            except Exception:  # noqa: BLE001 - a bad write must not kill the drain
                logger.exception("store write failed")

    def _broadcast(self, batch: list[Event | CallUpdate]) -> None:
        for queue in self._subscribers:
            for item in batch:
                try:
                    queue.put_nowait(item)
                except asyncio.QueueFull:
                    break
