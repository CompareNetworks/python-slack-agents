"""Push-preferred async delivery: when a push webhook is registered for a task,
the token-dependent poller is not spawned (push delivers inbound, regardless of
later OAuth token expiry/revocation)."""

from unittest.mock import AsyncMock

import pytest

from slack_agents.a2a import agent as a2a_agent
from slack_agents.a2a.client import A2AResult
from slack_agents.a2a.push import save_record
from slack_agents.storage.sqlite import Provider as Sqlite

UCC = {"user_id": "U1", "channel_id": "C1", "thread_id": "T1"}


@pytest.fixture
async def store():
    s = Sqlite(path=":memory:")
    await s.initialize()
    yield s
    await s.close()


def _provider(push_enabled):
    p = a2a_agent.Provider(
        url="https://agent.example.com",
        server_id="a",
        push_notifications=push_enabled,
    )
    p._manager = AsyncMock()  # bypass initialize(); we only test the dispatch decision
    return p


def _nonterminal():
    return A2AResult(state="working", text="", context_id="c1", task_id="t1")


async def test_skips_poller_when_push_registered(store):
    p = _provider(push_enabled=True)
    await save_record(store, "t1", channel_id="C1", thread_id="T1", user_id="U1", token="tok")
    res = await p._handle_non_terminal(_nonterminal(), "a", UCC, store)
    p._manager.track.assert_not_awaited()  # push delivers; no token-dependent poll
    assert "Started a longer task" in res["content"]


async def test_polls_when_push_enabled_but_not_registered(store):
    # push_notifications=True but no record saved (e.g. no reachable PUBLIC_URL) →
    # polling is the only delivery path, so the poller must run.
    p = _provider(push_enabled=True)
    await p._handle_non_terminal(_nonterminal(), "a", UCC, store)
    p._manager.track.assert_awaited_once()


async def test_polls_when_push_disabled(store):
    p = _provider(push_enabled=False)
    await p._handle_non_terminal(_nonterminal(), "a", UCC, store)
    p._manager.track.assert_awaited_once()
