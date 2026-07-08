"""Tests for PerUserOAuth identity helpers: has_token + userinfo logging."""

import logging

import httpx
import pytest
from mcp.shared.auth import OAuthMetadata, OAuthToken

from slack_agents import PendingFlowsRegistry
from slack_agents.oauth.crypto import derive_subkeys
from slack_agents.oauth.discovery import DiscoveryResult
from slack_agents.oauth.flow import PerUserOAuth, _client_not_found
from slack_agents.oauth.storage import DBTokenStorage
from slack_agents.storage.base import OAuthClientRow
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


# ---------------------------------------------------------------------------
# Reaped-DCR-client self-heal
# ---------------------------------------------------------------------------


def _asm():
    return OAuthMetadata(
        issuer="https://idp.example.com/realms/x",
        authorization_endpoint="https://idp.example.com/realms/x/protocol/openid-connect/auth",
        token_endpoint="https://idp.example.com/realms/x/protocol/openid-connect/token",
        registration_endpoint="https://idp.example.com/realms/x/clients-registrations/openid-connect",
    )


def _mock_httpx(monkeypatch, handler):
    import slack_agents.oauth.flow as flowmod

    real = httpx.AsyncClient
    monkeypatch.setattr(
        flowmod.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler))
    )


async def _seed_client(store, puo, client_id):
    await store.put_oauth_client(
        "rs",
        puo._redirect_uri,
        OAuthClientRow(
            client_id=client_id,
            client_secret=None,
            metadata_json="{}",
            authorization_server="",
            created_at=1000,
            updated_at=1000,
        ),
    )


# --- classifier ---


def test_client_not_found_matches_keycloak_error():
    body = "<html><body class='kc-error'>We are sorry... Client not found</body></html>"
    assert _client_not_found(400, body) is True


def test_client_not_found_is_case_insensitive():
    assert _client_not_found(400, "CLIENT NOT FOUND") is True


def test_client_not_found_ignores_redirect_uri_mismatch():
    # Different 400 — handled by is_redirect_uri_mismatch, must NOT match here.
    assert _client_not_found(400, "Invalid parameter: redirect_uri") is False


def test_client_not_found_ignores_non_400_status():
    # A live client renders a 200 login page (or 302); never treat as "gone".
    assert _client_not_found(200, "Client not found") is False
    assert _client_not_found(302, "") is False


# --- liveness probe ---


async def test_liveness_probe_true_when_idp_accepts_client(store, monkeypatch):
    puo = _puo(store, DiscoveryResult(_asm(), None))
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, text="<html>login page</html>")

    _mock_httpx(monkeypatch, handler)
    assert await puo._client_registration_is_live("live-cid") is True
    assert "client_id=live-cid" in seen["url"]


async def test_liveness_probe_false_when_client_not_found(store, monkeypatch):
    puo = _puo(store, DiscoveryResult(_asm(), None))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="We are sorry... Client not found")

    _mock_httpx(monkeypatch, handler)
    assert await puo._client_registration_is_live("dead-cid") is False


async def test_liveness_probe_none_without_authorize_endpoint(store):
    puo = _puo(store, DiscoveryResult(None, None))  # no ASM at all
    assert await puo._client_registration_is_live("cid") is None


async def test_liveness_probe_none_on_network_error(store, monkeypatch):
    puo = _puo(store, DiscoveryResult(_asm(), None))

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    _mock_httpx(monkeypatch, handler)
    assert await puo._client_registration_is_live("cid") is None


# --- _ensure_client_registered self-heal ---


async def test_ensure_registered_reregisters_when_client_reaped(store, monkeypatch):
    puo = _puo(store, DiscoveryResult(_asm(), None, scope_catalog=["mcp:x:read"]))
    await _seed_client(store, puo, "dead-cid")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":  # liveness probe
            return httpx.Response(400, text="Client not found")
        # POST to the registration endpoint
        return httpx.Response(
            200,
            json={
                "client_id": "fresh-cid",
                "redirect_uris": [puo._redirect_uri],
                "token_endpoint_auth_method": "none",
            },
        )

    _mock_httpx(monkeypatch, handler)
    await puo._ensure_client_registered()

    row = await store.get_oauth_client("rs", puo._redirect_uri)
    assert row is not None
    assert row.client_id == "fresh-cid"


async def test_ensure_registered_keeps_live_client(store, monkeypatch):
    puo = _puo(store, DiscoveryResult(_asm(), None))
    await _seed_client(store, puo, "live-cid")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET", "must not re-register a live client"
        return httpx.Response(200, text="<login>")

    _mock_httpx(monkeypatch, handler)
    await puo._ensure_client_registered()

    row = await store.get_oauth_client("rs", puo._redirect_uri)
    assert row.client_id == "live-cid"


async def test_ensure_registered_keeps_client_on_probe_network_error(store, monkeypatch):
    # Safety: a transient probe failure must NOT wipe a shared client.
    puo = _puo(store, DiscoveryResult(_asm(), None))
    await _seed_client(store, puo, "keep-cid")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            raise httpx.ConnectError("boom")
        raise AssertionError("must not re-register when the probe is inconclusive")

    _mock_httpx(monkeypatch, handler)
    await puo._ensure_client_registered()

    row = await store.get_oauth_client("rs", puo._redirect_uri)
    assert row.client_id == "keep-cid"
