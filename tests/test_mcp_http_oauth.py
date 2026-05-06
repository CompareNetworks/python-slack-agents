"""Tests for tools.mcp_http_oauth.Provider — auth-flow orchestration."""

import asyncio
import secrets
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.shared.auth import OAuthToken

from slack_agents import (
    FrameworkContext,
    OAuthCallbackResult,
    PendingFlowsRegistry,
)
from slack_agents.oauth.crypto import derive_subkeys
from slack_agents.storage.sqlite import Provider as SqliteProvider
from slack_agents.tools.mcp_http_oauth import Provider as McpHttpOAuthProvider


@pytest.fixture
async def framework_ctx():
    storage = SqliteProvider(path=":memory:")
    await storage.initialize()
    state_key, token_key = derive_subkeys(secrets.token_bytes(32))
    ctx = FrameworkContext(
        bot_token="xoxb-test",
        agent_name="testagent",
        slack_client=AsyncMock(),
        storage=storage,
        pending_flows=PendingFlowsRegistry(),
    )
    # Stash crypto on the ctx so the provider can find it without env access in tests.
    ctx._state_key = state_key  # type: ignore[attr-defined]
    ctx._token_key = token_key  # type: ignore[attr-defined]
    ctx._public_url = "https://agent.example.com"  # type: ignore[attr-defined]
    yield ctx
    await storage.close()


class TestProviderInitialization:
    def test_constructs_with_required_args(self, framework_ctx):
        p = McpHttpOAuthProvider(
            url="https://srv.example.com/mcp",
            allowed_functions=[".*"],
            framework_ctx=framework_ctx,
        )
        assert p._url == "https://srv.example.com/mcp"
        assert p._auth_timeout == 300


class TestAuthPromptOnMissingToken:
    async def test_redirect_handler_sends_ephemeral_and_registers_pending_flow(self, framework_ctx):
        p = McpHttpOAuthProvider(
            url="https://srv.example.com/mcp",
            allowed_functions=[".*"],
            framework_ctx=framework_ctx,
            server_id="my-mcp",
        )
        # Build a per-user OAuth handle (the unit under test).
        handle = p._oauth_handle_for_user("U1", "C1", "1.2")
        await handle.redirect_handler(
            "https://idp.example.com/authorize?state=SDK_STATE_123&client_id=x"
        )
        # An ephemeral was posted with our /oauth/start URL.
        framework_ctx.slack_client.chat_postEphemeral.assert_awaited_once()
        kwargs = framework_ctx.slack_client.chat_postEphemeral.await_args.kwargs
        button_url = kwargs["blocks"][1]["elements"][0]["url"]
        assert button_url.startswith("https://agent.example.com/oauth/start/")
        # And a pending future is registered keyed by the SDK state.
        assert "SDK_STATE_123" in framework_ctx.pending_flows._flows


class TestCallbackHandler:
    async def test_resolves_with_code_and_state(self, framework_ctx):
        p = McpHttpOAuthProvider(
            url="https://srv.example.com/mcp",
            allowed_functions=[".*"],
            framework_ctx=framework_ctx,
            server_id="my-mcp",
            auth_timeout=2,
        )
        handle = p._oauth_handle_for_user("U1", "C1", None)
        await handle.redirect_handler("https://idp.example.com/authorize?state=ST_OK&client_id=x")

        # Resolve from another task.
        async def resolver():
            await asyncio.sleep(0.05)
            framework_ctx.pending_flows.resolve(
                "ST_OK", OAuthCallbackResult(code="AUTHCODE", state="ST_OK")
            )

        resolver_task = asyncio.create_task(resolver())
        code, state = await handle.callback_handler()
        await resolver_task
        assert code == "AUTHCODE"
        assert state == "ST_OK"

    async def test_timeout_raises(self, framework_ctx):
        p = McpHttpOAuthProvider(
            url="https://srv.example.com/mcp",
            allowed_functions=[".*"],
            framework_ctx=framework_ctx,
            server_id="my-mcp",
            auth_timeout=1,
        )
        handle = p._oauth_handle_for_user("U1", "C1", None)
        await handle.redirect_handler("https://idp.example.com/authorize?state=ST_LATE&client_id=x")
        with pytest.raises(asyncio.TimeoutError):
            await handle.callback_handler()

    async def test_idp_user_level_error_raises_user_authorization_denied(self, framework_ctx):
        """User-level OAuth error codes (access_denied, invalid_scope, etc.)
        raise UserAuthorizationDenied so the caller can produce a friendly
        "your account doesn't have permission" message instead of a system error.
        """
        from slack_agents.tools.mcp_http_oauth import UserAuthorizationDenied

        p = McpHttpOAuthProvider(
            url="https://srv.example.com/mcp",
            allowed_functions=[".*"],
            framework_ctx=framework_ctx,
            server_id="my-mcp",
            auth_timeout=2,
        )
        handle = p._oauth_handle_for_user("U1", "C1", None)
        await handle.redirect_handler("https://idp.example.com/authorize?state=ST_DENY&client_id=x")
        framework_ctx.pending_flows.resolve(
            "ST_DENY",
            OAuthCallbackResult(
                code=None,
                state="ST_DENY",
                error="access_denied",
                error_description="user denied",
            ),
        )
        with pytest.raises(UserAuthorizationDenied) as excinfo:
            await handle.callback_handler()
        assert excinfo.value.code == "access_denied"
        assert excinfo.value.description == "user denied"

    async def test_idp_system_error_raises_oauth_flow_error(self, framework_ctx):
        """System-level OAuth errors (anything not in USER_LEVEL_CODES) still
        raise OAuthFlowError so the caller produces a support-attention message.
        """
        from mcp.client.auth.exceptions import OAuthFlowError

        p = McpHttpOAuthProvider(
            url="https://srv.example.com/mcp",
            allowed_functions=[".*"],
            framework_ctx=framework_ctx,
            server_id="my-mcp",
            auth_timeout=2,
        )
        handle = p._oauth_handle_for_user("U1", "C1", None)
        await handle.redirect_handler(
            "https://idp.example.com/authorize?state=ST_BROKEN&client_id=x"
        )
        framework_ctx.pending_flows.resolve(
            "ST_BROKEN",
            OAuthCallbackResult(code=None, state="ST_BROKEN", error="server_error"),
        )
        with pytest.raises(OAuthFlowError):
            await handle.callback_handler()


class TestCallToolHappyPath:
    async def test_cached_token_no_auth_flow_triggered(self, framework_ctx):
        """If a token is already cached, no ephemeral should be sent."""
        p = McpHttpOAuthProvider(
            url="https://srv.example.com/mcp",
            allowed_functions=[".*"],
            framework_ctx=framework_ctx,
            server_id="my-mcp",
        )
        # Pre-populate a token via DBTokenStorage.
        from slack_agents.oauth.storage import DBTokenStorage

        store = DBTokenStorage(
            backend=framework_ctx.storage,
            user_id="U1",
            server_id="my-mcp",
            token_key=p._token_key,
        )
        await store.set_tokens(
            OAuthToken(
                access_token="EXISTING",
                token_type="Bearer",
                expires_in=3600,
                refresh_token=None,
                scope="",
            )
        )
        # Patch out the actual MCP HTTP call to return a synthetic success.
        with patch.object(
            p,
            "_call_mcp_with_token",
            AsyncMock(return_value={"content": "ok", "is_error": False, "files": []}),
        ):
            result = await p.call_tool(
                tool_name="search",
                arguments={"q": "x"},
                user_conversation_context={
                    "user_id": "U1",
                    "user_name": "u",
                    "user_handle": "u",
                    "channel_id": "C1",
                    "channel_name": "c",
                    "thread_id": "1.2",
                },
                storage=framework_ctx.storage,
            )
        assert result["is_error"] is False
        # No ephemeral posted.
        framework_ctx.slack_client.chat_postEphemeral.assert_not_awaited()


class TestCallToolErrors:
    async def test_mcp_call_failure_returns_sanitized_error(self, framework_ctx):
        """When `_call_mcp_with_token` raises a non-permission failure (refresh
        failed, network error, etc.), `call_tool` returns a JSON-encoded
        structured system error with the server name, exception summary, UTC
        timestamp, and `needs_support: true`.
        """
        import json as _json

        p = McpHttpOAuthProvider(
            url="https://srv.example.com/mcp",
            allowed_functions=[".*"],
            framework_ctx=framework_ctx,
            server_id="my-mcp",
            auth_timeout=1,
        )

        async def fake_call(*args, **kwargs):
            raise asyncio.TimeoutError()

        with patch.object(p, "_call_mcp_with_token", side_effect=fake_call):
            result = await p.call_tool(
                tool_name="search",
                arguments={},
                user_conversation_context={
                    "user_id": "U1",
                    "user_name": "u",
                    "user_handle": "u",
                    "channel_id": "C1",
                    "channel_name": "c",
                    "thread_id": None,
                },
                storage=framework_ctx.storage,
            )
        assert result["is_error"] is True
        payload = _json.loads(result["content"])
        assert payload["error"] == "system_error"
        assert payload["server"] == "my-mcp"
        assert payload["recovery"] == "contact_support"
        assert payload["tool"] == "search"
        assert "timestamp_utc" in payload["details"]
        assert "exception" in payload["details"]


class TestScopeMergeHook:
    """The httpx response hook augments WWW-Authenticate scope= so the SDK's
    next authorize request includes the OIDC baseline + the user's currently
    granted scopes + whatever the server signaled."""

    async def test_parse_and_replace_helpers(self):
        from slack_agents.tools.mcp_http_oauth import (
            _parse_www_auth_scope,
            _replace_www_auth_scope,
        )

        h = (
            'Bearer resource_metadata="https://srv/.well-known/x", '
            'error="insufficient_scope", scope="mcp:test:write"'
        )
        assert _parse_www_auth_scope(h) == "mcp:test:write"
        h2 = _replace_www_auth_scope(h, "openid offline_access mcp:test:read mcp:test:write")
        assert 'scope="openid offline_access mcp:test:read mcp:test:write"' in h2
        # Other params preserved
        assert 'resource_metadata="https://srv/.well-known/x"' in h2
        assert 'error="insufficient_scope"' in h2

    async def test_hook_merges_baseline_cached_and_hint(self, framework_ctx):
        """End-to-end behavior of the augmenting response hook.

        Setup:
          - User has a cached token granting mcp:test:read.
          - Server returns 403 with WWW-Authenticate scope="mcp:test:write"
            (delta-only — server is stateless and doesn't echo back read).

        Expected after hook:
          - Header is rewritten so scope= contains the union:
            openid offline_access mcp:test:read mcp:test:write.
        """
        from slack_agents.oauth.storage import DBTokenStorage

        p = McpHttpOAuthProvider(
            url="https://srv.example.com/mcp",
            allowed_functions=[".*"],
            framework_ctx=framework_ctx,
            server_id="my-mcp",
        )
        # Pre-populate cached token with read scope.
        await DBTokenStorage(
            backend=framework_ctx.storage,
            user_id="U1",
            server_id="my-mcp",
            token_key=p._token_key,
        ).set_tokens(
            OAuthToken(
                access_token="cached",
                token_type="Bearer",
                expires_in=3600,
                refresh_token=None,
                scope="mcp:test:read",
            )
        )

        # Build a fake httpx.Response with a 403 + WWW-Authenticate hint.
        original_header = (
            'Bearer resource_metadata="https://srv/.well-known/x", '
            'error="insufficient_scope", scope="mcp:test:write"'
        )
        response = MagicMock()
        response.status_code = 403
        response.url = "https://srv.example.com/mcp"
        response.headers = {"WWW-Authenticate": original_header}

        hook = p._make_auth_response_hook("U1")
        await hook(response)

        new_header = response.headers["WWW-Authenticate"]
        # Server's params are preserved; only scope= changed.
        assert 'resource_metadata="https://srv/.well-known/x"' in new_header
        assert 'error="insufficient_scope"' in new_header
        # scope= now contains baseline + cached + hint.
        scope_value = new_header.split('scope="', 1)[1].split('"', 1)[0]
        scopes = set(scope_value.split())
        assert scopes == {
            "openid",
            "offline_access",
            "mcp:test:read",
            "mcp:test:write",
        }

    async def test_hook_no_scope_param_is_passthrough(self, framework_ctx):
        """If the server didn't include scope= in WWW-Authenticate, the hook
        leaves the header alone (the SDK falls back to PRM for scope strategy)."""

        p = McpHttpOAuthProvider(
            url="https://srv.example.com/mcp",
            allowed_functions=[".*"],
            framework_ctx=framework_ctx,
            server_id="my-mcp",
        )
        original = 'Bearer resource_metadata="https://srv/.well-known/x"'
        response = MagicMock()
        response.status_code = 401
        response.url = "https://srv.example.com/mcp"
        response.headers = {"WWW-Authenticate": original}
        hook = p._make_auth_response_hook("U1")
        await hook(response)
        assert response.headers["WWW-Authenticate"] == original

    async def test_hook_ignores_non_auth_responses(self, framework_ctx):
        """The hook is a no-op for 200/302/etc."""

        p = McpHttpOAuthProvider(
            url="https://srv.example.com/mcp",
            allowed_functions=[".*"],
            framework_ctx=framework_ctx,
            server_id="my-mcp",
        )
        response = MagicMock()
        response.status_code = 200
        response.url = "https://srv.example.com/mcp"
        response.headers = {"WWW-Authenticate": 'Bearer scope="anything"'}
        hook = p._make_auth_response_hook("U1")
        await hook(response)
        # Header unchanged.
        assert response.headers["WWW-Authenticate"] == 'Bearer scope="anything"'


class TestScopeNotGrantedDetection:
    """When the SDK's step-up auth flow completes but the user's account was
    only granted a subset of the requested scopes (because their role doesn't
    include the rest), the resource server returns a final 403. We detect that
    by comparing the cached token's scope against the server's WWW-Authenticate
    challenge and surface as a user-level permission denial — not a system
    error."""

    async def test_call_tool_recognises_post_step_up_scope_shortage(self, framework_ctx):
        import json as _json

        from slack_agents.oauth.storage import DBTokenStorage

        p = McpHttpOAuthProvider(
            url="https://srv.example.com/mcp",
            allowed_functions=[".*"],
            framework_ctx=framework_ctx,
            server_id="my-mcp",
        )
        # User got read+write after the step-up flow (admin was silently dropped
        # by the IdP because the user's role doesn't include it).
        await DBTokenStorage(
            backend=framework_ctx.storage,
            user_id="U1",
            server_id="my-mcp",
            token_key=p._token_key,
        ).set_tokens(
            OAuthToken(
                access_token="t",
                token_type="Bearer",
                expires_in=3600,
                refresh_token=None,
                scope="openid offline_access mcp:test:read mcp:test:write",
            )
        )
        # Build a synthetic ExceptionGroup carrying a 403 with WWW-Authenticate
        # asking for admin (which the user doesn't have).

        resp = MagicMock()
        resp.status_code = 403
        resp.headers = {
            "WWW-Authenticate": (
                'Bearer error="insufficient_scope", '
                'scope="mcp:test:admin mcp:test:read mcp:test:write '
                'offline_access openid"'
            ),
        }
        http_err = httpx.HTTPStatusError(
            "Client error '403 Forbidden'", request=MagicMock(), response=resp
        )
        eg = BaseExceptionGroup("test", [http_err])

        async def fake_call(*args, **kwargs):
            raise eg

        with patch.object(p, "_call_mcp_with_token", side_effect=fake_call):
            result = await p.call_tool(
                tool_name="admin_thing",
                arguments={},
                user_conversation_context={
                    "user_id": "U1",
                    "user_name": "u",
                    "user_handle": "u",
                    "channel_id": "C1",
                    "channel_name": "c",
                    "thread_id": None,
                },
                storage=framework_ctx.storage,
            )
        assert result["is_error"] is True
        payload = _json.loads(result["content"])
        # Permission-denied framing — NOT system error.
        assert payload["error"] == "permission_denied"
        assert payload["code"] == "scope_not_granted"
        assert payload["server"] == "my-mcp"
        assert payload["recovery"] == "contact_admin"
        assert payload["tool"] == "admin_thing"
        details = payload["details"]
        assert "mcp:test:admin" in details["missing_scopes"]
        assert "mcp:test:read" in details["granted_scopes"]
        assert "mcp:test:write" in details["granted_scopes"]
        # OIDC plumbing scopes don't pollute the resource-scope sets.
        assert "openid" not in details["required_scopes"]
        assert "offline_access" not in details["required_scopes"]


# Need this import at module level for the test above.
import httpx  # noqa: E402
