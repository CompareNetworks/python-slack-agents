import asyncio

import pytest

from slack_agents.a2a.client import A2AResult
from slack_agents.a2a.delivery import AsyncTaskManager
from slack_agents.storage.sqlite import Provider as Sqlite


class FakeClient:
    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.calls = 0

    async def get_task(self, task_id):
        r = self.sequence[min(self.calls, len(self.sequence) - 1)]
        self.calls += 1
        return r


@pytest.fixture
async def store():
    s = Sqlite(path=":memory:")
    await s.initialize()
    yield s
    await s.close()


def _collector():
    """Return (list, async deliver-callable that appends to it)."""
    out = []

    async def deliver(**kw):
        out.append(kw)

    return out, deliver


def _record(task_id="t1"):
    return {
        "task_id": task_id,
        "context_id": "c1",
        "channel_id": "C1",
        "thread_id": "T1",
        "user_id": "U1",
        "created_at": 0.0,
    }


async def test_poll_until_completed_delivers_and_clears(store):
    client = FakeClient(
        [
            A2AResult("working", "", "c1", "t1"),
            A2AResult("completed", "done!", "c1", "t1"),
        ]
    )
    delivered, deliver = _collector()
    mgr = AsyncTaskManager(
        server_key="helper",
        client=client,
        storage=store,
        deliver=deliver,
        poll_interval=0.01,
        max_lifetime=5,
    )
    await mgr.track(_record())
    await asyncio.wait_for(mgr.wait_idle(), timeout=2)
    assert delivered and delivered[0]["text"] == "done!"
    assert delivered[0]["is_error"] is False
    assert await store.get("a2a:inflight:helper", "t1") is None


async def test_failed_delivers_error(store):
    client = FakeClient([A2AResult("failed", "kaboom", "c1", "t1")])
    delivered, deliver = _collector()
    mgr = AsyncTaskManager("helper", client, store, deliver, poll_interval=0.01, max_lifetime=5)
    await mgr.track(_record())
    await asyncio.wait_for(mgr.wait_idle(), timeout=2)
    assert delivered[0]["is_error"] is True


async def test_lifetime_exceeded_delivers_timeout(store):
    client = FakeClient([A2AResult("working", "", "c1", "t1")])
    delivered, deliver = _collector()
    mgr = AsyncTaskManager("helper", client, store, deliver, poll_interval=0.01, max_lifetime=0.05)
    await mgr.track(_record())
    await asyncio.wait_for(mgr.wait_idle(), timeout=2)
    assert delivered[0]["is_error"] is True
    assert "did not finish in time" in delivered[0]["text"].lower()


async def test_resume_respawns_pollers_from_storage(store):
    await store.set("a2a:inflight:helper", "t7", _record("t7"))
    client = FakeClient([A2AResult("completed", "resumed", "c1", "t7")])
    delivered, deliver = _collector()
    mgr = AsyncTaskManager("helper", client, store, deliver, poll_interval=0.01, max_lifetime=5)
    await mgr.resume()
    await asyncio.wait_for(mgr.wait_idle(), timeout=2)
    assert delivered[0]["text"] == "resumed"
