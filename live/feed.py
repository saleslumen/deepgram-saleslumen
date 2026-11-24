"""In-process live event feed with bounded replay and subscriber queues."""

import asyncio
from collections import deque

from live.events import CallStarted, LiveEvent

REPLAY_LIMIT = 64
STREAM_END = object()


class Feed:
    def __init__(self) -> None:
        self._replay: deque[LiveEvent] = deque(maxlen=REPLAY_LIMIT)
        self._subscribers: list[asyncio.Queue[object]] = []
        self._current_call_id: str | None = None
        self._call_started: CallStarted | None = None
    def publish(self, event: LiveEvent) -> None:
        if isinstance(event, CallStarted):
            if self._current_call_id is not None and event.call_id == self._current_call_id:
                return
            if self._current_call_id is not None and event.call_id != self._current_call_id:
                self._replay.clear()
                self._current_call_id = event.call_id
                self._call_started = event
                for queue in list(self._subscribers):
                    self._end(queue)
                return
            self._current_call_id = event.call_id
            self._call_started = event
            self._fan_out(event)
            return
        if self._current_call_id is None:
            return
        if event.call_id != self._current_call_id:
            return
        self._replay.append(event)
        self._fan_out(event)
    def subscribe(self) -> asyncio.Queue[object]:
        queue: asyncio.Queue[object] = asyncio.Queue(maxsize=REPLAY_LIMIT + 1)
        if self._call_started is not None:
            queue.put_nowait(self._call_started)
        for item in self._replay:
            queue.put_nowait(item)
        self._subscribers.append(queue)
        return queue
    def unsubscribe(self, queue: asyncio.Queue[object]) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)
    def _end(self, queue: asyncio.Queue[object]) -> None:
        self.unsubscribe(queue)
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        queue.put_nowait(STREAM_END)
    def _fan_out(self, event: LiveEvent) -> None:
        for queue in list(self._subscribers):
            self._offer(queue, event)
    def _offer(self, queue: asyncio.Queue[object], event: LiveEvent) -> None:
        if queue.full():
            self._end(queue)
            return
        queue.put_nowait(event)


feed = Feed()