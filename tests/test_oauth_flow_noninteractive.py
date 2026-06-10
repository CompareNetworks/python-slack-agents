import pytest

from slack_agents import PendingFlowsRegistry
from slack_agents.oauth.errors import ReauthRequired
from slack_agents.oauth.flow import PerUserHandle


@pytest.mark.asyncio
async def test_noninteractive_redirect_raises_reauth_required():
    handle = PerUserHandle(
        user_id="U1",
        channel_id="C1",
        thread_id="T1",
        server_id="srv",
        pending_flows=PendingFlowsRegistry(),
        slack_client=None,
        state_key=b"0" * 32,
        auth_timeout=300,
        public_url="https://a.example.com",
        interactive=False,
    )
    with pytest.raises(ReauthRequired):
        await handle.redirect_handler("https://idp/authorize?state=S")


@pytest.mark.asyncio
async def test_interactive_default_does_not_raise_reauth(monkeypatch):
    sent = {}

    async def fake_prompt(**kwargs):
        sent.update(kwargs)

    monkeypatch.setattr("slack_agents.oauth.flow.send_auth_prompt", fake_prompt)
    handle = PerUserHandle(
        user_id="U1",
        channel_id="C1",
        thread_id="T1",
        server_id="srv",
        pending_flows=PendingFlowsRegistry(),
        slack_client=object(),
        state_key=b"0" * 32,
        auth_timeout=300,
        public_url="https://a.example.com",
    )
    await handle.redirect_handler("https://idp/authorize?state=S")
    assert sent  # prompt path was taken
