"""End-to-end: proxy + tool through the real agent loop (sync path)."""

import pytest

from slack_agents.a2a import agent as a2a_agent
from slack_agents.a2a.client import A2AResult
from slack_agents.a2a.proxy import Provider as Proxy
from slack_agents.agent_loop import run_agent_loop_streaming
from slack_agents.llm.base import Message
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
    def __init__(self, result):
        self.result = result

    async def resolve_card(self):
        return {"name": "Helper", "description": "d", "skills_text": ""}

    async def send(self, message, context_id, task_id=None, files=None, push_config=None):
        return self.result

    async def close(self):
        pass


@pytest.fixture
async def store():
    s = Sqlite(path=":memory:")
    await s.initialize()
    yield s
    await s.close()


async def test_proxy_plus_tool_sync_roundtrip(monkeypatch, store):
    monkeypatch.setattr(
        a2a_agent,
        "A2AClient",
        lambda **kw: FakeClient(A2AResult("completed", "FINAL ANSWER", "c", "t")),
    )
    tool = a2a_agent.Provider(url="http://x", allowed_functions=[".*"], name="mya2a")
    await tool.initialize()
    proxy = Proxy(model="mya2a", max_input_tokens=10000)

    text = ""
    async for ev in run_agent_loop_streaming(
        llm=proxy,
        messages=[Message(role="user", content="do it")],
        tool_providers=[tool],
        user_conversation_context=UCC,
        storage=store,
    ):
        if getattr(ev, "type", None) == "text_delta":
            text += ev.text
    assert text == "FINAL ANSWER"
    await tool.close()
