"""Live integration test against a real A2A server (env-gated, agent-agnostic).

This is the only test that exercises `client.py`'s real a2a-sdk wire calls
(card resolution, send, response normalization) end-to-end. It is SKIPPED unless
`A2A_TEST_URL` points at a running A2A agent, so CI and contributors without a
server are unaffected.

Spin up the official reference agent and run it:

    git clone --depth 1 https://github.com/a2aproject/a2a-samples /tmp/a2a-samples
    cd /tmp/a2a-samples/samples/python/agents/helloworld
    # the upstream Containerfile binds 127.0.0.1 inside the container; bind 0.0.0.0:
    sed -i '' "s/host='127.0.0.1'/host='0.0.0.0'/" __main__.py
    docker build -f Containerfile -t helloworld-a2a-server .
    docker run -d -p 9999:9999 helloworld-a2a-server

    A2A_TEST_URL=http://127.0.0.1:9999 pytest tests/a2a/test_integration.py -v

Assertions are intentionally agent-agnostic (any conformant agent that returns a
terminal reply passes), so the test works against helloworld or any other agent.
"""

import os

import pytest

from slack_agents.a2a.client import A2AClient, classify

A2A_TEST_URL = os.environ.get("A2A_TEST_URL")

pytestmark = pytest.mark.skipif(
    not A2A_TEST_URL,
    reason="set A2A_TEST_URL to a running A2A agent to run live integration tests",
)


@pytest.fixture
async def client():
    c = A2AClient(url=A2A_TEST_URL)
    await c.resolve_card()
    yield c
    await c.close()


async def test_resolve_card_returns_identity():
    c = A2AClient(url=A2A_TEST_URL)
    try:
        card = await c.resolve_card()
        assert card["name"]  # a conformant agent advertises a non-empty name
    finally:
        await c.close()


async def test_send_returns_a_terminal_reply(client):
    r = await client.send("integration-test ping", None)
    assert classify(r.state) == "terminal"
    assert r.text  # the agent returned some text (echo / ack / result)
    assert r.context_id  # the server assigned a context for the exchange
