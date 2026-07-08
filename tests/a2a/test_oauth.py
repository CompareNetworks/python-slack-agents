"""Tests for per-user OAuth wiring in a2a.agent.Provider.

Scope: WIRING only — all network is mocked.  No full browser/IdP OAuth dance.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from slack_agents.a2a import agent as a2a_agent
from slack_agents.a2a.client import A2AClient, A2AResult
from slack_agents.storage.sqlite import Provider as Sqlite

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

UCC = {
    "user_id": "U1",
    "user_name": "u",
    "user_handle": "u",
    "channel_id": "C1",
    "channel_name": "c",
    "thread_id": "T1",
}

# A deterministic base64-encoded 32-byte root key for monkeypatching
_ROOT_KEY_B64 = base64.b64encode(b"x" * 32).decode()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_oauth_card():
    """Build a real protobuf AgentCard that advertises an OAuth2 authorization_code scheme."""
    from a2a.types.a2a_pb2 import (
        AgentCard,
        AuthorizationCodeOAuthFlow,
        OAuth2SecurityScheme,
        OAuthFlows,
        SecurityRequirement,
        SecurityScheme,
        StringList,
    )

    ac = AuthorizationCodeOAuthFlow(
        authorization_url="https://as.example.com/auth",
        token_url="https://as.example.com/token",
        scopes={"agent:x:read": "read access"},
    )
    scheme = SecurityScheme(
        oauth2_security_scheme=OAuth2SecurityScheme(
            flows=OAuthFlows(authorization_code=ac),
            oauth2_metadata_url="https://as.example.com/.well-known/openid-configuration",
        )
    )
    card = AgentCard(name="SecureAgent", description="secure")
    card.security_schemes["oauth2"].CopyFrom(scheme)
    req = SecurityRequirement()
    req.schemes["oauth2"].CopyFrom(StringList(list=["agent:x:read"]))
    card.security_requirements.append(req)
    return card


def _make_plain_card():
    """Build a real AgentCard with no security scheme (no oauth2)."""
    from a2a.types.a2a_pb2 import AgentCard

    return AgentCard(name="PlainAgent", description="plain")


async def _noop_deliver_async_result(**kw):
    pass


def _make_framework_ctx(store):
    """Build a SimpleNamespace that looks like FrameworkContext for tests."""
    return SimpleNamespace(
        agent_name="test-agent",
        storage=store,
        slack_client=MagicMock(),
        pending_flows=MagicMock(),
        _public_url="https://a.example.com",
        deliver_async_result=_noop_deliver_async_result,
    )


@pytest.fixture
async def store():
    s = Sqlite(path=":memory:")
    await s.initialize()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# Test 1: oauth2 mode builds PerUserOAuth on initialize
# ---------------------------------------------------------------------------


async def test_oauth_mode_builds_per_user_oauth_on_initialize(monkeypatch, store):
    """Provider with auth.type==oauth2 should set _oauth to a PerUserOAuth after initialize()."""
    from slack_agents.oauth.flow import PerUserOAuth

    oauth_card = _make_oauth_card()
    fctx = _make_framework_ctx(store)
    monkeypatch.setenv("OAUTH_SECRET_KEY", _ROOT_KEY_B64)

    # Patch A2AClient.resolve_card to set the card and return info dict
    async def fake_resolve_card(self_client):
        self_client._card = oauth_card
        return {"name": "SecureAgent", "description": "secure", "skills_text": ""}

    with patch.object(A2AClient, "resolve_card", fake_resolve_card):
        p = a2a_agent.Provider(
            url="https://agent.example.com",
            server_id="secure-agent",
            auth={"type": "oauth2"},
            framework_ctx=fctx,
        )
        await p.initialize()

    assert p._oauth_mode is True
    assert isinstance(p._oauth, PerUserOAuth)


# ---------------------------------------------------------------------------
# Test 2: oauth2 mode with a card lacking oauth2 raises a clear error
# ---------------------------------------------------------------------------


async def test_oauth_mode_raises_when_card_has_no_oauth_scheme(monkeypatch, store):
    """If the card has no oauth2 scheme, _setup_oauth should raise a ValueError."""
    plain_card = _make_plain_card()
    fctx = _make_framework_ctx(store)
    monkeypatch.setenv("OAUTH_SECRET_KEY", _ROOT_KEY_B64)

    async def fake_resolve_card(self_client):
        self_client._card = plain_card
        return {"name": "PlainAgent", "description": "plain", "skills_text": ""}

    with patch.object(A2AClient, "resolve_card", fake_resolve_card):
        p = a2a_agent.Provider(
            url="https://agent.example.com",
            server_id="plain-agent",
            auth={"type": "oauth2"},
            framework_ctx=fctx,
        )
        with pytest.raises(ValueError, match="oauth2"):
            await p.initialize()


# ---------------------------------------------------------------------------
# Test 3: call_tool routes through a per-user client and caches it
# ---------------------------------------------------------------------------


async def test_call_tool_uses_per_user_client_and_caches(monkeypatch, store):
    """call_tool in oauth mode should call build_user_a2a_client once and cache the result."""
    oauth_card = _make_oauth_card()
    fctx = _make_framework_ctx(store)
    monkeypatch.setenv("OAUTH_SECRET_KEY", _ROOT_KEY_B64)

    # Build a fake user A2AClient whose send() returns a terminal result
    fake_user_client = MagicMock(spec=A2AClient)
    fake_user_client.send = AsyncMock(
        return_value=A2AResult(state="completed", text="ok", context_id="c1", task_id=None)
    )
    fake_user_client.close = AsyncMock()

    build_mock = AsyncMock(return_value=fake_user_client)

    async def fake_resolve_card(self_client):
        self_client._card = oauth_card
        return {"name": "SecureAgent", "description": "secure", "skills_text": ""}

    with (
        patch.object(A2AClient, "resolve_card", fake_resolve_card),
        patch("slack_agents.a2a.agent.build_user_a2a_client", build_mock),
    ):
        p = a2a_agent.Provider(
            url="https://agent.example.com",
            server_id="secure-agent",
            auth={"type": "oauth2"},
            framework_ctx=fctx,
        )
        await p.initialize()

        # First call — should build and cache
        res1 = await p.call_tool("secure-agent", {"message": "hello"}, UCC, store)
        # Second call same user — should reuse cached client
        res2 = await p.call_tool("secure-agent", {"message": "world"}, UCC, store)

    assert res1["content"] == "ok"
    assert res2["content"] == "ok"
    # build_user_a2a_client called ONCE (cache hit on second call)
    build_mock.assert_awaited_once()
    # The fake user client's send was called TWICE
    assert fake_user_client.send.await_count == 2

    await p.close()


# ---------------------------------------------------------------------------
# Test 4: Static path unchanged — _get_client returns shared self._client
# ---------------------------------------------------------------------------


async def test_static_auth_returns_shared_client(monkeypatch, store):
    """A Provider with static bearer auth should use _get_client -> self._client (identity)."""

    class FakeClient:
        async def resolve_card(self):
            return {"name": "Helper", "description": "d", "skills_text": ""}

        async def send(self, message, context_id, task_id=None, files=None, push_config=None):
            return A2AResult(state="completed", text="hi", context_id="c", task_id=None)

        async def close(self):
            pass

    fake = FakeClient()
    monkeypatch.setattr(a2a_agent, "A2AClient", lambda **kw: fake)

    p = a2a_agent.Provider(
        url="http://x",
        server_id="helper",
        auth={"type": "bearer", "token": "t"},
    )
    await p.initialize()

    assert p._oauth_mode is False
    # _get_client should return the SAME object as self._client
    resolved = await p._get_client(UCC)
    assert resolved is p._client

    await p.close()


async def test_no_auth_returns_shared_client(monkeypatch, store):
    """A Provider with no auth should also route through the shared self._client."""

    class FakeClient:
        async def resolve_card(self):
            return {"name": "Helper", "description": "d", "skills_text": ""}

        async def send(self, message, context_id, task_id=None, files=None, push_config=None):
            return A2AResult(state="completed", text="hi", context_id="c", task_id=None)

        async def close(self):
            pass

    fake = FakeClient()
    monkeypatch.setattr(a2a_agent, "A2AClient", lambda **kw: fake)

    p = a2a_agent.Provider(url="http://x", server_id="helper")
    await p.initialize()

    assert p._oauth_mode is False
    resolved = await p._get_client(UCC)
    assert resolved is p._client

    await p.close()


# ---------------------------------------------------------------------------
# Test: OAuth-specific send errors map to actionable results (not contact-support)
# ---------------------------------------------------------------------------


async def _build_oauth_provider(monkeypatch, store, send_exc):
    """Build an initialized oauth-mode Provider whose per-user client.send raises send_exc."""
    oauth_card = _make_oauth_card()
    fctx = _make_framework_ctx(store)
    monkeypatch.setenv("OAUTH_SECRET_KEY", _ROOT_KEY_B64)

    fake_user_client = MagicMock(spec=A2AClient)
    fake_user_client.send = AsyncMock(side_effect=send_exc)
    fake_user_client.close = AsyncMock()
    build_mock = AsyncMock(return_value=fake_user_client)

    async def fake_resolve_card(self_client):
        self_client._card = oauth_card
        return {"name": "SecureAgent", "description": "secure", "skills_text": ""}

    with (
        patch.object(A2AClient, "resolve_card", fake_resolve_card),
        patch("slack_agents.a2a.agent.build_user_a2a_client", build_mock),
    ):
        p = a2a_agent.Provider(
            url="https://agent.example.com",
            server_id="secure-agent",
            auth={"type": "oauth2"},
            framework_ctx=fctx,
        )
        await p.initialize()
        result = await p.call_tool("secure-agent", {"message": "hi"}, UCC, store)
    return p, result


async def test_call_tool_user_denied_maps_to_permission_error(monkeypatch, store):
    import json

    from slack_agents.oauth.errors import UserAuthorizationDenied

    denied = UserAuthorizationDenied(
        code="scope_not_granted",
        required_scopes=["agent:x:write"],
        granted_scopes=["agent:x:read"],
    )
    _p, result = await _build_oauth_provider(monkeypatch, store, denied)
    assert result["is_error"] is True
    payload = json.loads(result["content"])
    assert payload["code"] == "scope_not_granted"
    assert payload["details"]["missing_scopes"] == ["agent:x:write"]


async def test_call_tool_redirect_uri_mismatch_clears_registration(monkeypatch, store):
    import json

    p, result = await _build_oauth_provider(
        monkeypatch, store, Exception("Invalid parameter: redirect_uri")
    )
    assert result["is_error"] is True
    payload = json.loads(result["content"])
    assert payload["code"] == "redirect_uri_mismatch"
    # The cached per-user client was evicted so the next call rebuilds it.
    assert "U1" not in p._user_clients


# ---------------------------------------------------------------------------
# Test: a static (no-auth) provider that 401s against an auth-requiring agent
# returns an actionable "configure auth" error, not a raw system failure.
# ---------------------------------------------------------------------------


async def test_static_401_with_auth_card_returns_actionable_hint(store):
    import json

    import httpx

    oauth_card = _make_oauth_card()  # advertises an oauth2 scheme
    fctx = _make_framework_ctx(store)

    p = a2a_agent.Provider(
        url="https://agent.example.com",
        server_id="mya2a",
        auth={},  # empty → static, no auth (the reported footgun)
        framework_ctx=fctx,
    )
    assert p._oauth_mode is False

    # Stub the shared static client: card resolved, send raises a wrapped 401.
    req = httpx.Request("POST", "https://agent.example.com")
    cause = httpx.HTTPStatusError("401", request=req, response=httpx.Response(401, request=req))
    wrapped = RuntimeError("HTTP Error 401: Unauthorized")
    wrapped.__cause__ = cause
    fake_client = MagicMock(spec=A2AClient)
    fake_client.card = oauth_card
    fake_client.send = AsyncMock(side_effect=wrapped)
    p._client = fake_client

    result = await p.call_tool("mya2a", {"message": "hi"}, UCC, store)
    payload = json.loads(result["content"])
    assert payload["code"] == "auth_required"
    assert "oauth2" in payload["message"]


# ---------------------------------------------------------------------------
# Reaped-DCR-client self-heal via the A2A oauth path (shares PerUserOAuth)
# ---------------------------------------------------------------------------


async def test_a2a_oauth_reregisters_reaped_client(monkeypatch):
    import httpx
    from mcp.shared.auth import OAuthMetadata

    from slack_agents import FrameworkContext, PendingFlowsRegistry
    from slack_agents.a2a.oauth import build_per_user_oauth
    from slack_agents.oauth.crypto import derive_subkeys
    from slack_agents.oauth.discovery import DiscoveryResult
    from slack_agents.storage.base import OAuthClientRow

    storage = Sqlite(path=":memory:")
    await storage.initialize()
    ctx = FrameworkContext(
        bot_token="xoxb",
        agent_name="t",
        slack_client=None,
        storage=storage,
        pending_flows=PendingFlowsRegistry(),
    )
    sk, tk = derive_subkeys(b"0" * 32)
    puo = build_per_user_oauth(
        card_oauth={
            "metadata_url": "https://idp.example.com/.well-known/oauth-authorization-server",
            "scopes": ["agent:read"],
            "required_scopes": ["agent:read"],
        },
        server_url="https://remote.example.com/a2a",
        server_id="remote-a2a",
        framework_ctx=ctx,
        state_key=sk,
        token_key=tk,
        public_url="https://a.example.com",
        auth_timeout=300,
    )
    asm = OAuthMetadata(
        issuer="https://idp.example.com/realms/x",
        authorization_endpoint="https://idp.example.com/realms/x/protocol/openid-connect/auth",
        token_endpoint="https://idp.example.com/realms/x/protocol/openid-connect/token",
        registration_endpoint="https://idp.example.com/realms/x/clients-registrations/openid-connect",
    )
    puo._cached = DiscoveryResult(asm, None, scope_catalog=["agent:read"])
    await storage.put_oauth_client(
        "remote-a2a",
        puo._redirect_uri,
        OAuthClientRow(
            client_id="dead-cid",
            client_secret=None,
            metadata_json="{}",
            authorization_server="",
            created_at=1000,
            updated_at=1000,
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":  # liveness probe → client is gone
            return httpx.Response(400, text="Client not found")
        return httpx.Response(  # DCR re-registration
            200,
            json={
                "client_id": "fresh-cid",
                "redirect_uris": [puo._redirect_uri],
                "token_endpoint_auth_method": "none",
            },
        )

    import slack_agents.oauth.flow as flowmod

    real = httpx.AsyncClient
    monkeypatch.setattr(
        flowmod.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler))
    )

    await puo._ensure_client_registered()

    row = await storage.get_oauth_client("remote-a2a", puo._redirect_uri)
    assert row is not None
    assert row.client_id == "fresh-cid"
    await storage.close()
