"""A refresh token the IdP refuses ends the call at once with a fresh sign-in link."""

import asyncio
import json

import httpx
import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata, OAuthToken

from slack_agents import PendingFlowsRegistry
from slack_agents.oauth import flow
from slack_agents.oauth.crypto import derive_subkeys
from slack_agents.oauth.discovery import DiscoveryResult
from slack_agents.oauth.errors import (
    ReauthRequired,
    SignInExpired,
    sign_in_expired_tool_result,
)
from slack_agents.oauth.flow import PerUserOAuth
from slack_agents.oauth.storage import DBTokenStorage
from slack_agents.storage.sqlite import Provider as Sqlite

TOKEN_URL = "https://idp.example.com/token"
RS_URL = "https://rs.example.com/a2a"


class _StubDiscovery:
    async def discover(self):
        raise AssertionError("discovery is pre-cached")


@pytest.fixture
async def store():
    s = Sqlite(path=":memory:")
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
async def puo(store):
    sk, tk = derive_subkeys(b"0" * 32)
    p = PerUserOAuth(
        server_url=RS_URL,
        server_id="agent-geo",
        agent_name="t",
        storage=store,
        pending_flows=PendingFlowsRegistry(),
        slack_client=None,
        state_key=sk,
        token_key=tk,
        redirect_uri="https://a.example.com/oauth/callback",
        public_url="https://a.example.com",
        auth_timeout=300,
        discovery=_StubDiscovery(),
    )
    p._cached = DiscoveryResult(
        resource_metadata=None,
        authorization_server_metadata=OAuthMetadata(
            issuer="https://idp.example.com",
            authorization_endpoint="https://idp.example.com/auth",
            token_endpoint=TOKEN_URL,
        ),
        scope_catalog=[],
        required_scopes=[],
    )
    storage = DBTokenStorage(
        backend=store,
        user_id="__system__",
        server_id="agent-geo",
        redirect_uri=p._redirect_uri,
        token_key=tk,
    )
    await storage.set_client_info(
        OAuthClientInformationFull(client_id="c1", redirect_uris=[p._redirect_uri])
    )
    # An expired access token with a refresh token, as stored after 60 days.
    await p._token_storage("U1").set_tokens(
        OAuthToken(
            access_token="old",
            token_type="Bearer",
            expires_in=-60,
            refresh_token="dead-refresh",
            scope="openid offline_access agent:agent-geo:read",
        )
    )
    return p


def _client(provider, token_response):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if str(request.url) == TOKEN_URL:
            return token_response
        return httpx.Response(200, json={"ok": True})

    return httpx.AsyncClient(auth=provider, transport=httpx.MockTransport(handler)), seen


async def test_refused_refresh_deletes_token_prompts_and_fails_fast(puo, store, monkeypatch):
    started = []
    monkeypatch.setattr(puo, "start_sign_in", lambda *a: started.append(a))
    provider = await puo.build_provider("U1", "C1", "T1")
    client, seen = _client(
        provider,
        httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "Token is not active"}
        ),
    )

    with pytest.raises(SignInExpired) as info:
        async with client:
            await asyncio.wait_for(client.post(RS_URL, json={}), timeout=5)

    assert info.value.prompted is True
    assert isinstance(info.value, ReauthRequired)  # the poller's existing handling still applies
    assert seen == [TOKEN_URL]  # never sent to the resource, never waited for a callback
    assert await store.get_oauth_token("U1", "agent-geo") is None
    assert started == [("U1", "C1", "T1", {"openid", "offline_access", "agent:agent-geo:read"})]


async def test_noninteractive_refused_refresh_does_not_prompt(puo, store, monkeypatch):
    started = []
    monkeypatch.setattr(puo, "start_sign_in", lambda *a: started.append(a))
    provider = await puo.build_provider("U1", "C1", "T1", interactive=False)
    client, _ = _client(provider, httpx.Response(400, json={"error": "invalid_token"}))

    with pytest.raises(SignInExpired) as info:
        async with client:
            await client.post(RS_URL, json={})

    assert info.value.prompted is False
    assert started == []
    assert await store.get_oauth_token("U1", "agent-geo") is None


async def test_other_refresh_failures_keep_sdk_behaviour(puo, store, monkeypatch):
    monkeypatch.setattr(puo, "start_sign_in", lambda *a: pytest.fail("must not prompt"))
    provider = await puo.build_provider("U1", "C1", "T1", interactive=False)
    client, _ = _client(provider, httpx.Response(503, text="unavailable"))

    # A transient IdP failure is not a dead sign-in: no SignInExpired, token kept.
    async with client:
        response = await client.post(RS_URL, json={})
    assert response.status_code == 200
    assert await store.get_oauth_token("U1", "agent-geo") is not None


async def test_background_sign_in_stores_new_token_with_previous_scopes(puo, store, monkeypatch):
    requested_scope = {}

    async def perform_authorization(self):
        requested_scope["scope"] = self.context.client_metadata.scope
        return httpx.Request("POST", TOKEN_URL, data={"code": "abc"})

    monkeypatch.setattr(flow._PerUserProvider, "_perform_authorization", perform_authorization)
    real_client = httpx.AsyncClient
    fresh = {"access_token": "new", "token_type": "Bearer", "expires_in": 600}
    fresh["refresh_token"] = "r2"
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=fresh))
    monkeypatch.setattr(flow.httpx, "AsyncClient", lambda **kw: real_client(transport=transport))
    await store.delete_oauth_token("U1", "agent-geo")

    puo.start_sign_in("U1", "C1", "T1", {"agent:agent-geo:read", "offline_access"})
    await asyncio.gather(*puo._sign_ins)

    expected = {"agent:agent-geo:read", "offline_access", "openid"}
    assert set(requested_scope["scope"].split()) >= expected
    tokens = await puo._token_storage("U1").get_tokens()
    assert tokens.access_token == "new"
    assert tokens.refresh_token == "r2"


def test_tool_result_tells_the_user_to_sign_in_and_stop():
    result = sign_in_expired_tool_result(SignInExpired("agent-geo", prompted=True), tool="geo")
    payload = json.loads(result["content"])
    assert result["is_error"] is True
    assert payload["code"] == "sign_in_expired"
    assert payload["recovery"] == "abort"
    assert "sign-in link" in payload["message"]
    assert "sign in, then ask again" in payload["message"]
