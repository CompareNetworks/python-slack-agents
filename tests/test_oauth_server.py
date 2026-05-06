"""Tests for the in-process OAuth HTTP listener."""

import asyncio
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from slack_agents import PendingFlowsRegistry
from slack_agents.oauth.server import build_app
from slack_agents.oauth.state import NonceReplayCache, StatePayload, encode

KEY = b"\x07" * 32


@pytest.fixture
async def app_client():
    pending = PendingFlowsRegistry()
    nonce_cache = NonceReplayCache()
    app = build_app(
        state_key=KEY,
        nonce_cache=nonce_cache,
        pending_flows=pending,
    )
    async with TestServer(app) as srv, TestClient(srv) as client:
        yield client, pending


class TestStartRoute:
    async def test_valid_state_redirects_to_authorize_url(self, app_client):
        client, _ = app_client
        signed = encode(
            StatePayload(
                user_id="U",
                server_id="srv",
                authorize_url="https://idp.example.com/authorize?state=abc",
                exp=int(time.time()) + 60,
            ),
            KEY,
        )
        resp = await client.get(f"/oauth/start/{signed}", allow_redirects=False)
        assert resp.status == 302
        assert resp.headers["Location"].startswith("https://idp.example.com/authorize")

    async def test_invalid_signature_returns_400(self, app_client):
        client, _ = app_client
        resp = await client.get("/oauth/start/not-a-real-token", allow_redirects=False)
        assert resp.status == 400

    async def test_expired_state_returns_400(self, app_client):
        client, _ = app_client
        signed = encode(
            StatePayload(
                user_id="U",
                server_id="srv",
                authorize_url="https://idp.example.com/authorize",
                exp=int(time.time()) - 1,
            ),
            KEY,
        )
        resp = await client.get(f"/oauth/start/{signed}", allow_redirects=False)
        assert resp.status == 400


class TestCallbackRoute:
    async def test_resolves_pending_future(self, app_client):
        client, pending = app_client
        fut = pending.register("sdk-state-xyz")
        resp = await client.get("/oauth/callback?code=AUTHCODE&state=sdk-state-xyz")
        assert resp.status == 200
        result = await asyncio.wait_for(fut, timeout=1.0)
        assert result.code == "AUTHCODE"
        assert result.state == "sdk-state-xyz"

    async def test_no_pending_flow_returns_expired_page(self, app_client):
        client, _ = app_client
        resp = await client.get("/oauth/callback?code=X&state=unknown")
        assert resp.status == 200
        body = await resp.text()
        assert "expired" in body.lower()

    async def test_idp_error_resolves_with_error(self, app_client):
        client, pending = app_client
        fut = pending.register("sdk-state-deny")
        resp = await client.get(
            "/oauth/callback?error=access_denied&error_description=user+denied&state=sdk-state-deny"
        )
        assert resp.status == 200
        body = await resp.text()
        # The browser should not say "you're authenticated" when the IdP redirected
        # back with ?error=... — it should render an error page that includes the
        # IdP's error code and description.
        assert "authenticated" not in body.lower() or "failed" in body.lower()
        assert "access_denied" in body
        assert "user denied" in body
        result = await asyncio.wait_for(fut, timeout=1.0)
        assert result.code is None
        assert result.error == "access_denied"


class TestUnknownRoutes:
    async def test_unknown_returns_404(self, app_client):
        client, _ = app_client
        resp = await client.get("/random")
        assert resp.status == 404
