import pytest

from slack_agents.a2a.client import A2AResult
from slack_agents.a2a.delivery import AsyncTaskManager
from slack_agents.oauth.errors import ReauthRequired
from slack_agents.storage.sqlite import Provider as SqliteProvider


class _FakeClient:
    def __init__(self, result=None, raise_exc=None):
        self._result = result
        self._raise = raise_exc
        self.closed = False

    async def get_task(self, tid):
        if self._raise:
            raise self._raise
        return self._result

    async def close(self):
        self.closed = True


def _record():
    return {
        "task_id": "t1",
        "context_id": "c1",
        "channel_id": "C1",
        "thread_id": "T1",
        "user_id": "U1",
        "created_at": 0.0,
    }


@pytest.mark.asyncio
async def test_client_factory_used_and_closed_on_success():
    storage = SqliteProvider(path=":memory:")
    await storage.initialize()
    delivered = []

    async def deliver(**kw):
        delivered.append(kw)

    client = _FakeClient(
        result=A2AResult(state="completed", text="done", context_id="c1", task_id="t1")
    )
    built = []

    async def factory(record):
        built.append(record["user_id"])
        return client

    mgr = AsyncTaskManager(
        server_key="srv",
        client=None,
        client_factory=factory,
        storage=storage,
        deliver=deliver,
        poll_interval=0.01,
    )
    await mgr.track(_record())
    await mgr.wait_idle()
    assert built == ["U1"]
    assert client.closed is True
    assert delivered and delivered[0]["text"] == "done"
    await storage.close()


@pytest.mark.asyncio
async def test_reauth_required_delivers_session_expired():
    storage = SqliteProvider(path=":memory:")
    await storage.initialize()
    delivered = []

    async def deliver(**kw):
        delivered.append(kw)

    client = _FakeClient(raise_exc=ReauthRequired("nope"))

    async def factory(record):
        return client

    mgr = AsyncTaskManager(
        server_key="srv",
        client=None,
        client_factory=factory,
        storage=storage,
        deliver=deliver,
        poll_interval=0.01,
    )
    await mgr.track(_record())
    await mgr.wait_idle()
    assert delivered and delivered[0]["is_error"] is True
    assert "session" in delivered[0]["text"].lower() or "re-ask" in delivered[0]["text"].lower()
    await storage.close()
