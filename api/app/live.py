"""Live incident feed for Server-Sent Events.

One poller per division per API instance, however many browsers are watching: each poll
is diffed against the last and only the changes are fanned out to subscriber queues.
Instances need no shared state — every instance polls DynamoDB independently.
"""

import asyncio
import logging
from collections.abc import Callable

log = logging.getLogger("railpulse.live")

FINGERPRINT_FIELDS = ("status", "severity", "occurrences", "escalation_role", "escalation_state", "last_seen")


def fingerprint(item: dict) -> tuple:
    return tuple(str(item.get(f)) for f in FINGERPRINT_FIELDS)


def diff(prev: dict[str, tuple], items: list[dict]) -> tuple[dict[str, tuple], list[dict]]:
    """Return (new state, events). An incident leaving the active set has been resolved."""
    state = {i["incident_id"]: fingerprint(i) for i in items}
    events = []
    for item in items:
        iid = item["incident_id"]
        if iid not in prev:
            events.append({"kind": "created", "incident": item})
        elif prev[iid] != state[iid]:
            events.append({"kind": "updated", "incident": item})
    events += [{"kind": "closed", "incident": {"incident_id": iid}} for iid in prev.keys() - state.keys()]
    return state, events


class LiveHub:
    def __init__(self, fetch_active: Callable[[str], list[dict]], interval_s: float = 3.0, queue_size: int = 100) -> None:
        self._fetch = fetch_active
        self._interval = interval_s
        self._queue_size = queue_size
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def subscribe(self, division: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subs.setdefault(division, set()).add(q)
        if division not in self._tasks or self._tasks[division].done():
            self._tasks[division] = asyncio.create_task(self._poll(division))
        return q

    def unsubscribe(self, division: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(division, set())
        subs.discard(q)
        if not subs and (task := self._tasks.pop(division, None)):
            task.cancel()

    def subscriber_count(self, division: str) -> int:
        return len(self._subs.get(division, ()))

    async def _poll(self, division: str) -> None:
        state: dict[str, tuple] | None = None
        while True:
            try:
                items = await asyncio.to_thread(self._fetch, division)
                if state is None:
                    state = {i["incident_id"]: fingerprint(i) for i in items}  # baseline, no events
                else:
                    state, events = diff(state, items)
                    for ev in events:
                        self.publish(division, ev)
            except asyncio.CancelledError:
                raise
            except Exception:  # keep the feed alive through a transient DynamoDB error
                log.exception("live poll failed for %s", division)
            await asyncio.sleep(self._interval)

    def publish(self, division: str, event: dict) -> None:
        for q in list(self._subs.get(division, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # a stalled browser drops events rather than blocking everyone; it re-syncs on reconnect
