"""Regression: the in-process OAuth ingress must actually start for an
A2A-OAuth agent.

This exercises `SlackAgent._start_ingress_if_needed`, which decodes
`OAUTH_SECRET_KEY` and builds the `/oauth/*` app — a startup path that had no
test coverage and shipped a `base64.b64decode(key, True)` bug (True landed on
the positional `altchars` arg instead of `validate=`), crashing startup the
first time an A2A-OAuth agent ran it.
"""

import base64
import secrets
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from slack_agents import FrameworkContext, PendingFlowsRegistry


def _config(tools):
    from slack_agents.config import AgentConfig

    return AgentConfig(
        version="1.0.0",
        slack={"bot_token": "xoxb-test", "app_token": "xapp-test"},
        llm={"type": "slack_agents.llm.anthropic", "model": "test", "api_key": "k"},
        storage={"type": "slack_agents.storage.sqlite"},
        access={"type": "slack_agents.access.allow_all"},
        tools=tools,
    )


def _agent(config):
    from slack_agents.slack.agent import SlackAgent

    # __init__ loads only the llm + access plugins (tools load later in start()).
    with patch("slack_agents.slack.agent.load_plugin") as mock_load:
        mock_load.side_effect = [MagicMock(), MagicMock()]
        agent = SlackAgent(config, system_prompt="t", agent_name="test-agent")
    agent._framework_ctx = FrameworkContext(
        bot_token="xoxb-test",
        agent_name="test-agent",
        slack_client=MagicMock(),
        storage=MagicMock(),
        pending_flows=PendingFlowsRegistry(),
    )
    return agent


@pytest.mark.asyncio
async def test_ingress_starts_for_a2a_oauth_agent(monkeypatch):
    monkeypatch.setenv("OAUTH_SECRET_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    monkeypatch.setenv("PUBLIC_URL", "https://a.example.com")

    agent = _agent(
        _config(
            {
                "mya2a": {
                    "type": "slack_agents.a2a.agent",
                    "auth": {"type": "oauth2"},
                    "url": "https://agent.example.com",
                    "allowed_functions": [".*"],
                }
            }
        )
    )

    # Don't bind a real port — just prove the oauth-app build (incl. the
    # OAUTH_SECRET_KEY decode) runs cleanly.
    with (
        patch("aiohttp.web.AppRunner") as Runner,
        patch("aiohttp.web.TCPSite") as Site,
    ):
        Runner.return_value.setup = AsyncMock()
        Site.return_value.start = AsyncMock()
        await agent._start_ingress_if_needed()

    assert agent._framework_ctx._public_url == "https://a.example.com"


@pytest.mark.asyncio
async def test_no_ingress_for_static_a2a_agent(monkeypatch):
    monkeypatch.delenv("OAUTH_SECRET_KEY", raising=False)
    monkeypatch.delenv("PUBLIC_URL", raising=False)

    agent = _agent(
        _config(
            {
                "mya2a": {
                    "type": "slack_agents.a2a.agent",
                    "auth": {"type": "bearer", "token": "t"},
                    "url": "https://agent.example.com",
                    "allowed_functions": [".*"],
                }
            }
        )
    )

    # Static, no push → no ingress needed → returns without touching aiohttp.
    with (
        patch("aiohttp.web.AppRunner") as Runner,
        patch("aiohttp.web.TCPSite") as Site,
    ):
        await agent._start_ingress_if_needed()
        Runner.assert_not_called()
        Site.assert_not_called()
