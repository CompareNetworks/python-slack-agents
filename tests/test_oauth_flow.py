"""Tests for PerUserOAuth identity helpers: has_token + userinfo logging."""

import logging

import httpx
import pytest
from mcp.shared.auth import OAuthToken

from slack_agents import PendingFlowsRegistry
from slack_agents.oauth.crypto import derive_subkeys
from slack_agents.oauth.discovery import DiscoveryResult
from slack_agents.oauth.flow import PerUserOAuth
from slack_agents.oauth.storage import DBTokenStorage
from slack_agents.storage.sqlite import Provider as Sqlite


class _StubDiscovery:
    def __init__(self, result):
        self._r = result

    async def discover(self):
        return self._r


@pytest.fixture
async def store():
    s = Sqlite(path=":memory:")
    await s.initialize()
    yield s
    await s.close()


def _puo(store, result):
    sk, tk = derive_subkeys(b"0" * 32)
    puo = PerUserOAuth(
        server_url="https://rs.example.com/a2a",
        server_id="rs",
        agent_name="t",
        storage=store,
        pending_flows=PendingFlowsRegistry(),
        slack_client=None,
        state_key=sk,
        token_key=tk,
        redirect_uri="https://a.example.com/oauth/callback",
        public_url="https://a.example.com",
        auth_timeout=300,
        discovery=_StubDiscovery(result),
    )
    puo._cached = result  # pretend discovery already ran
    return puo


async def _store_token(puo, store, scope="openid profile"):
    await DBTokenStorage(
        backend=store,
        user_id="U1",
        server_id="rs",
        redirect_uri=puo._redirect_uri,
        token_key=puo._token_key,
    ).set_tokens(OAuthToken(access_token="AT", token_type="Bearer", scope=scope))


async def test_has_token_reflects_storage(store):
    puo = _puo(store, DiscoveryResult(None, None))
    assert await puo.has_token("U1") is False
    await _store_token(puo, store)
    assert await puo.has_token("U1") is True


async def test_log_user_info_fetches_and_logs_claims(store, caplog, monkeypatch):
    puo = _puo(
        store, DiscoveryResult(None, None, userinfo_endpoint="https://idp.example.com/userinfo")
    )
    await _store_token(puo, store)

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["url"] = str(request.url)
        return httpx.Response(
            200, json={"sub": "abc", "preferred_username": "eric", "email": "e@x.com"}
        )

    import slack_agents.oauth.flow as flowmod

    real = httpx.AsyncClient
    monkeypatch.setattr(
        flowmod.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler))
    )
    with caplog.at_level(logging.INFO, logger="slack_agents.oauth.flow"):
        await puo.log_user_info("U1")

    assert seen["auth"] == "Bearer AT"
    assert "userinfo" in seen["url"]
    assert any("eric" in r.getMessage() for r in caplog.records)


async def test_log_user_info_noop_without_endpoint(store):
    puo = _puo(store, DiscoveryResult(None, None))  # no userinfo_endpoint
    await _store_token(puo, store)
    await puo.log_user_info("U1")  # must not raise (no-op)


async def test_log_user_info_noop_without_token(store):
    puo = _puo(
        store, DiscoveryResult(None, None, userinfo_endpoint="https://idp.example.com/userinfo")
    )
    await puo.log_user_info("U1")  # no token stored → no-op, never raises
