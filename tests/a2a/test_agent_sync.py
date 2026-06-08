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
    def __init__(self, result):
        self.result = result
        self.sent = []

    async def resolve_card(self):
        return {"name": "Helper", "description": "d", "skills_text": ""}

    async def send(self, message, context_id, task_id=None, files=None, push_config=None):
        self.sent.append((message, context_id, task_id))
        return self.result

    async def close(self):
        pass


@pytest.fixture
async def store():
    s = Sqlite(path=":memory:")
    await s.initialize()
    yield s
    await s.close()


async def _provider(monkeypatch, result):
    fake = FakeClient(result)
    monkeypatch.setattr(a2a_agent, "A2AClient", lambda **kw: fake)
    p = a2a_agent.Provider(url="http://x", allowed_functions=[".*"], name="helper")
    await p.initialize()
    return p, fake


async def test_tool_def_is_single_freetext_tool(monkeypatch):
    p, _ = await _provider(monkeypatch, A2AResult("completed", "hi", "ctx", "t"))
    tools = p.tools
    assert len(tools) == 1
    assert tools[0]["name"] == "helper"
    assert tools[0]["input_schema"]["required"] == ["message"]


async def test_completed_returns_text_and_persists_context(monkeypatch, store):
    p, fake = await _provider(monkeypatch, A2AResult("completed", "the answer", "ctx9", "t1"))
    res = await p.call_tool("helper", {"message": "hello"}, UCC, store)
    assert res["is_error"] is False
    assert res["content"] == "the answer"
    assert fake.sent == [("hello", None, None)]
    # completed → contextId kept, taskId cleared
    assert await store.get("a2a:ctx:helper", "T1") == {"context_id": "ctx9"}


async def test_second_call_reuses_stored_context(monkeypatch, store):
    p, fake = await _provider(monkeypatch, A2AResult("completed", "x", "ctx9", "t1"))
    await p.call_tool("helper", {"message": "first"}, UCC, store)
    await p.call_tool("helper", {"message": "second"}, UCC, store)
    # second turn reuses contextId; taskId is None because the first task completed
    assert fake.sent[1] == ("second", "ctx9", None)


async def test_failed_state_returns_tool_error(monkeypatch, store):
    p, _ = await _provider(monkeypatch, A2AResult("failed", "boom", "c", "t"))
    res = await p.call_tool("helper", {"message": "x"}, UCC, store)
    assert res["is_error"] is True


async def test_input_required_relays_prompt_as_text(monkeypatch, store):
    p, _ = await _provider(monkeypatch, A2AResult("input-required", "which env?", "c", "t"))
    res = await p.call_tool("helper", {"message": "deploy"}, UCC, store)
    assert res["is_error"] is False
    assert res["content"] == "which env?"


async def test_input_required_persists_and_threads_taskid(monkeypatch, store):
    # A multi-turn task: the agent stays input-required, so the same taskId must be
    # threaded on the next turn (this is the guessing-game / same-secret fix).
    p, fake = await _provider(monkeypatch, A2AResult("input-required", "guess?", "ctxG", "taskG"))
    await p.call_tool("helper", {"message": "hello"}, UCC, store)
    assert await store.get("a2a:ctx:helper", "T1") == {"context_id": "ctxG", "task_id": "taskG"}
    await p.call_tool("helper", {"message": "5"}, UCC, store)
    # next turn continues the SAME task
    assert fake.sent[1] == ("5", "ctxG", "taskG")


async def test_terminal_clears_threaded_taskid(monkeypatch, store):
    # Mid-game state seeded; a completed response must thread the saved task, then clear it.
    await store.set("a2a:ctx:helper", "T1", {"context_id": "ctxG", "task_id": "taskG"})
    p, fake = await _provider(monkeypatch, A2AResult("completed", "you win", "ctxG", "taskG"))
    await p.call_tool("helper", {"message": "7"}, UCC, store)
    assert fake.sent[0] == ("7", "ctxG", "taskG")
    assert await store.get("a2a:ctx:helper", "T1") == {"context_id": "ctxG"}


async def test_forwards_uploads_and_returns_received_files(monkeypatch, store):
    out_file = {"data": b"hello", "filename": "out.txt", "mimeType": "text/plain"}
    captured = {}

    class FileClient:
        async def resolve_card(self):
            return {"name": "Helper", "description": "d", "skills_text": ""}

        async def send(self, message, context_id, task_id=None, files=None, push_config=None):
            captured["files"] = files
            return A2AResult("completed", "done", "c", "t", files=[out_file])

        async def close(self):
            pass

    monkeypatch.setattr(a2a_agent, "A2AClient", lambda **kw: FileClient())

    class Ctx:
        agent_name = "demo"
        slack_client = None
        storage = store
        deliver_async_result = None
        pending_uploads = {"T1": [{"data": b"abc", "filename": "in.csv", "mimeType": "text/csv"}]}

    p = a2a_agent.Provider(
        url="http://x", allowed_functions=[".*"], name="helper", framework_ctx=Ctx()
    )
    await p.initialize()
    res = await p.call_tool("helper", {"message": "go"}, UCC, store)
    # the user's attachment was forwarded to the agent
    assert captured["files"] == [{"data": b"abc", "filename": "in.csv", "mimeType": "text/csv"}]
    # the agent's returned file is surfaced on the ToolResult (framework uploads it)
    assert res["files"] == [out_file]
