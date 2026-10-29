from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, AsyncIterator

from .storage import Storage


class EventBus:
    """In-process pub/sub for run events, backed by durable SQLite events.

    SSE clients first receive stored events, then live events. The bus is intentionally
    simple: one process, many subscribers, bounded queues, no backpressure stalls.
    """

    def __init__(self, storage: Storage):
        self.storage = storage
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    async def publish(
        self,
        run_id: str,
        event_type: str,
        message: str = "",
        agent_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = self.storage.add_event(run_id, event_type, message, agent_id, data)
        # Single-threaded event loop: set copy/add/discard is safe without a lock.
        queues = list(self._subscribers.get(run_id, set()))
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest event for this slow subscriber.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except asyncio.QueueEmpty:
                    pass
        return event

    async def stream(
        self,
        run_id: str,
        after_id: int = 0,
    ) -> AsyncIterator[dict[str, Any]]:
        for event in self.storage.list_events(run_id, after_id=after_id):
            yield event
            after_id = int(event["id"])

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers[run_id].add(queue)
        try:
            while True:
                event = await queue.get()
                if int(event["id"]) <= after_id:
                    continue
                after_id = int(event["id"])
                yield event
        finally:
            self._subscribers[run_id].discard(queue)
