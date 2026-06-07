# tests/a2a/test_framework_delivery.py
import pytest


class FakeSlack:
    def __init__(self):
        self.posts = []

    async def chat_postMessage(self, **kw):
        self.posts.append(kw)


class RealLLM:  # no relays_async_raw → should re-enter the loop
    pass


class ProxyLLM:
    relays_async_raw = True


@pytest.mark.parametrize("llm,expect_reentry", [(RealLLM(), True), (ProxyLLM(), False)])
async def test_delivery_routes_by_llm_type(monkeypatch, llm, expect_reentry):
    from slack_agents.slack.agent import SlackAgent

    agent = SlackAgent.__new__(SlackAgent)  # bypass __init__; unit-test the method
    agent.llm = llm
    agent.agent_name = "demo"
    agent._slack_client = FakeSlack()
    ran = {}

    async def fake_run_turn(channel, thread, user_id, text):
        ran["args"] = (channel, thread, user_id, text)

    agent._run_turn = fake_run_turn
    await agent.deliver_async_result(
        channel_id="C1", thread_id="T1", user_id="U1", text="result text", is_error=False
    )
    if expect_reentry:
        assert ran["args"][:3] == ("C1", "T1", "U1")
        assert "result text" in ran["args"][3]
        assert agent._slack_client.posts == []
    else:
        assert agent._slack_client.posts and agent._slack_client.posts[0]["thread_ts"] == "T1"
        assert "args" not in ran
