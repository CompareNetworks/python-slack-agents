import pytest

from slack_agents.a2a import agent as a2a_agent
from slack_agents.a2a.client import A2AResult
from slack_agents.storage.sqlite import Provider as Sqlite

UCC = {
    "user_id": "U1",
    "user_name": "u",
    "user_handle": "u",
    "channel_id": "C1",
    "channel_name": "c",
    "thread_id": "T1",
}


class FakeClient:
    def __init__(self, send_result):
        self.send_result = send_result

    async def resolve_card(self):
        return {"name": "Helper", "description": "d", "skills_text": ""}

    async def send(self, message, context_id, task_id=None, files=None, push_config=None):
        return self.send_result

    async def get_task(self, task_id):
        return A2AResult("working", "", "c1", task_id)

    async def close(self):
        pass


@pytest.fixture
async def store():
    s = Sqlite(path=":memory:")
    await s.initialize()
    yield s
    await s.close()


async def test_working_writes_inflight_record_and_returns_ack(monkeypatch, store):
    fake = FakeClient(A2AResult("working", "", "ctxA", "taskA"))
    monkeypatch.setattr(a2a_agent, "A2AClient", lambda **kw: fake)
    delivered = []

    class Ctx:
        agent_name = "demo"
        slack_client = None
        storage = store

        @staticmethod
        async def deliver_async_result(**kw):
            delivered.append(kw)

    p = a2a_agent.Provider(
        url="http://x",
        server_id="helper",
        framework_ctx=Ctx(),
        poll_interval=0.01,
        max_task_lifetime=5,
    )
    await p.initialize()
    res = await p.call_tool("helper", {"message": "long job"}, UCC, store)
    assert res["is_error"] is False
    assert "result" in res["content"].lower() or "ready" in res["content"].lower()
    rec = await store.get("a2a:inflight:helper", "taskA")
    assert rec is not None and rec["thread_id"] == "T1" and rec["channel_id"] == "C1"
    await p.close()
