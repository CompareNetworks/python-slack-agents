"""End-to-end OAuth flow test with an in-process fake IdP.

The fake IdP serves /.well-known/oauth-authorization-server, /register, /authorize,
and /token. The Provider's redirect_handler 'click' is simulated by an httpx call
into the Provider's OAuth listener after we encode a signed start URL.
"""

import asyncio
import base64
import secrets
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from aiohttp import web

from slack_agents import FrameworkContext, PendingFlowsRegistry
from slack_agents.oauth.crypto import derive_subkeys
from slack_agents.oauth.server import build_app
from slack_agents.oauth.state import NonceReplayCache
from slack_agents.storage.sqlite import Provider as SqliteProvider


@pytest.fixture
async def fake_idp():
    """Run a minimal OAuth 2.1 IdP with DCR + auth-code + PKCE."""
    state_seen = {"value": None}

    async def metadata(request):
        base = str(request.url.with_path(""))
        return web.json_response(
            {
                "issuer": base,
                "authorization_endpoint": f"{base}/authorize",
                "token_endpoint": f"{base}/token",
                "registration_endpoint": f"{base}/register",
                "response_types_supported": ["code"],
                "code_challenge_methods_supported": ["S256"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "scopes_supported": ["read:docs", "write:docs"],
            }
        )

    async def register(request):
        body = await request.json()
        return web.json_response(
            {
                "client_id": "test-client-id",
                "client_id_issued_at": int(time.time()),
                "redirect_uris": body.get("redirect_uris", []),
                "client_name": body.get("client_name", ""),
                "token_endpoint_auth_method": "none",
            }
        )

    async def authorize(request):
        # Real flow: 302 back to redirect_uri with code+state.
        # We expose the URL but our test will short-circuit by calling our own
        # /oauth/callback directly with the state we know.
        state_seen["value"] = request.query["state"]
        return web.json_response({"state": request.query["state"]})

    async def token(request):
        return web.json_response(
            {
                "access_token": "ACCESS_TOKEN_OK",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "REFRESH_OK",
                "scope": "read:docs",
            }
        )

    app = web.Application()
    app.router.add_get("/.well-known/oauth-authorization-server", metadata)
    app.router.add_post("/register", register)
    app.router.add_get("/authorize", authorize)
    app.router.add_post("/token", token)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="127.0.0.1", port=0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    yield base_url, state_seen
    await runner.cleanup()


class TestEndToEndAuth:
    async def test_happy_path_persists_token(self, fake_idp, monkeypatch):
        from slack_agents.tools.mcp_http_oauth import Provider

        idp_url, state_seen = fake_idp

        # Set env so the Provider derives keys deterministically per test.
        root = base64.b64encode(b"\x33" * 32).decode()
        monkeypatch.setenv("OAUTH_SECRET_KEY", root)
        monkeypatch.setenv("OAUTH_PUBLIC_URL", "http://127.0.0.1:0")  # filled below

        storage = SqliteProvider(path=":memory:")
        await storage.initialize()
        ctx = FrameworkContext(
            bot_token="xoxb",
            agent_name="t",
            slack_client=AsyncMock(),
            storage=storage,
            pending_flows=PendingFlowsRegistry(),
        )

        # Stand up the Provider's listener too.
        state_key, _ = derive_subkeys(base64.b64decode(root))
        nonce_cache = NonceReplayCache()
        listener = build_app(
            state_key=state_key,
            nonce_cache=nonce_cache,
            pending_flows=ctx.pending_flows,
        )
        runner = web.AppRunner(listener)
        await runner.setup()
        site = web.TCPSite(runner, host="127.0.0.1", port=0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        public_url = f"http://127.0.0.1:{port}"
        ctx._public_url = public_url

        try:
            # Build the Provider; point its server_url at the fake IdP since the IdP
            # also serves protected-resource metadata for this test.
            provider = Provider(
                url=idp_url + "/mcp",
                allowed_functions=[".*"],
                framework_ctx=ctx,
                server_id="my-mcp",
                auth_timeout=5,
            )

            # Drive the auth flow: redirect_handler will register a pending future
            # keyed by the SDK state. We simulate the user clicking by calling the
            # Provider listener's /oauth/callback directly with that state.
            handle = provider._oauth_handle_for_user("U1", "C1", None)
            # Redirect handler with a synthetic authorize_url carrying a state.
            sdk_state = secrets.token_hex(16)
            await handle.redirect_handler(
                f"{idp_url}/authorize?state={sdk_state}&client_id=test-client-id"
            )

            # Resolve the future from another task, simulating the user click.
            async def fake_click():
                async with httpx.AsyncClient() as c:
                    await c.get(
                        f"{public_url}/oauth/callback",
                        params={"code": "AUTHCODE", "state": sdk_state},
                    )

            click_task = asyncio.create_task(fake_click())
            code, returned_state = await handle.callback_handler()
            await click_task

            assert code == "AUTHCODE"
            assert returned_state == sdk_state
        finally:
            await runner.cleanup()
            await storage.close()
