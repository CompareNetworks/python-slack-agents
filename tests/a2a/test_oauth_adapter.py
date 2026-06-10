from types import SimpleNamespace

from slack_agents.a2a.oauth import build_per_user_oauth
from slack_agents.oauth.flow import PerUserOAuth


def test_build_per_user_oauth_returns_peruser():
    fctx = SimpleNamespace(
        agent_name="t", storage=object(), pending_flows=object(), slack_client=object()
    )
    puo = build_per_user_oauth(
        card_oauth={
            "metadata_url": "https://as/.well-known/openid-configuration",
            "scopes": ["agent:x:read"],
            "required_scopes": ["agent:x:read"],
            "authorization_url": "https://as/auth",
            "token_url": "https://as/token",
        },
        server_url="https://agent.example.com",
        server_id="agent.example.com",
        framework_ctx=fctx,
        state_key=b"0" * 32,
        token_key=b"1" * 32,
        public_url="https://a.example.com",
        auth_timeout=300,
    )
    assert isinstance(puo, PerUserOAuth)


async def test_build_user_a2a_client_closes_httpx_on_resolve_failure(monkeypatch):
    """If resolve_card() fails, the per-user httpx client must be closed (no leak)."""
    from unittest.mock import AsyncMock, patch

    from slack_agents.a2a import oauth as a2a_oauth

    fake_provider = object()
    oauth = AsyncMock()
    oauth.build_provider = AsyncMock(return_value=fake_provider)
    oauth.auth_response_hook = lambda uid: lambda resp: None

    closed = {"httpx": False, "client": False}

    class _FakeHttpx:
        async def aclose(self):
            closed["httpx"] = True

    class _FakeA2AClient:
        def __init__(self, **kw):
            pass

        async def resolve_card(self):
            raise RuntimeError("card fetch boom")

        async def close(self):
            closed["client"] = True

    with (
        patch.object(a2a_oauth.httpx, "AsyncClient", lambda **kw: _FakeHttpx()),
        patch.object(a2a_oauth, "A2AClient", _FakeA2AClient),
    ):
        with __import__("pytest").raises(RuntimeError, match="card fetch boom"):
            await a2a_oauth.build_user_a2a_client(
                oauth=oauth,
                url="http://x",
                timeout=30,
                user_id="U1",
                channel_id="C1",
                thread_id="T1",
            )
    assert closed["httpx"] is True
    assert closed["client"] is True
