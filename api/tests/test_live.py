import asyncio

from app.live import LiveHub, diff, fingerprint


def inc(iid, **kw):
    return {"incident_id": iid, "status": "OPEN", "severity": 2, "occurrences": 1, **kw}


def test_diff_reports_created_updated_closed():
    prev = {"a": fingerprint(inc("a")), "b": fingerprint(inc("b"))}
    state, events = diff(prev, [inc("a"), inc("b", severity=1), inc("c")])
    assert [(e["kind"], e["incident"]["incident_id"]) for e in events] == [("updated", "b"), ("created", "c")]
    _, events = diff(state, [inc("c")])
    assert sorted((e["kind"], e["incident"]["incident_id"]) for e in events) == [("closed", "a"), ("closed", "b")]


def test_unchanged_poll_is_silent():
    prev = {"a": fingerprint(inc("a"))}
    assert diff(prev, [inc("a")])[1] == []


def test_hub_shares_one_poller_and_stops_when_last_viewer_leaves():
    calls = []
    snapshots = [[inc("a")], [inc("a"), inc("b")], [inc("a", status="ACKNOWLEDGED"), inc("b")]]

    def fetch(division):
        calls.append(division)
        return snapshots[min(len(calls) - 1, len(snapshots) - 1)]

    async def scenario():
        hub = LiveHub(fetch, interval_s=0.01)
        q1, q2 = hub.subscribe("DLI"), hub.subscribe("DLI")
        got = [await asyncio.wait_for(q1.get(), 1), await asyncio.wait_for(q1.get(), 1)]
        assert q2.qsize() == 2  # both viewers got the same events from one poller
        hub.unsubscribe("DLI", q1)
        hub.unsubscribe("DLI", q2)
        await asyncio.sleep(0.05)
        before = len(calls)
        await asyncio.sleep(0.05)
        assert len(calls) == before  # poller cancelled
        return got

    got = asyncio.run(scenario())
    assert [(e["kind"], e["incident"]["incident_id"]) for e in got] == [("created", "b"), ("updated", "a")]
