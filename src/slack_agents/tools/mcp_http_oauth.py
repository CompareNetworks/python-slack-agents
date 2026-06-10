"""OAuth-authenticated MCP-over-HTTP tool provider.

Per-Slack-user tokens. The first inbound message from a user without a cached
token causes a `chat.postEphemeral` to be posted in their thread with an
"Authenticate" button linking to the in-process callback server. The user
clicks, completes the flow at the upstream IdP, the callback delivers the auth
code back to a suspended coroutine, the SDK exchanges it for tokens, and the
agent proceeds.

Authentication uses Dynamic Client Registration (RFC 7591) — the upstream
authorization server must support it. Static pre-registered client credentials
are not supported here; if a server requires them, this provider would need to
be extended.

Refresh-token use, full re-auth on refresh failure, and scope step-up on
401/403-with-WWW-Authenticate are all handled by the SDK's `OAuthClientProvider`
once it's attached as the httpx auth on every request to the MCP server.
"""

from __future__ import annotations

import base64
import logging
import os
from urllib.parse import urlparse

import httpx
import mcp
from mcp.client.streamable_http import streamable_http_client
from mcp.types import BlobResourceContents, EmbeddedResource, ImageContent

from slack_agents import FrameworkContext
from slack_agents.oauth.crypto import derive_subkeys
from slack_agents.oauth.discovery import PrmDiscovery
from slack_agents.oauth.errors import (
    AuthSetupError,
    UserAuthorizationDenied,
    find_user_authorization_denied,
    is_redirect_uri_mismatch,
    message_from_tool_error,
    record_error,
    user_denied_tool_result,
)
from slack_agents.oauth.flow import PerUserOAuth
from slack_agents.oauth.scopes import (
    OIDC_BASELINE_SCOPES,
    detect_missing_scopes,
)
from slack_agents.oauth.storage import DBTokenStorage
from slack_agents.storage.base import BaseStorageProvider
from slack_agents.tools.base import (
    ERROR_SYSTEM_ERROR,
    RECOVERY_RETRY,
    BaseToolProvider,
    ToolResult,
    make_tool_error,
)
from slack_agents.tools.mcp_http import _uri_to_filename

logger = logging.getLogger(__name__)


_DEFAULT_AUTH_TIMEOUT = 300
_DEFAULT_INIT_RETRIES = [5, 10, 30]


class Provider(BaseToolProvider):
    """OAuth-authenticated MCP-over-HTTP provider (per-Slack-user tokens, DCR-only)."""

    def __init__(
        self,
        url: str,
        allowed_functions: list[str],
        *,
        framework_ctx: FrameworkContext,
        server_id: str | None = None,
        init_retries: list[int | float] | None = None,
        auth_timeout: int = _DEFAULT_AUTH_TIMEOUT,
    ) -> None:
        super().__init__(allowed_functions)
        self._url = url
        self._framework_ctx = framework_ctx
        self._server_id = server_id or urlparse(url).netloc
        self._init_retries = init_retries if init_retries is not None else _DEFAULT_INIT_RETRIES
        self._auth_timeout = auth_timeout
        root = self._load_root_key()
        self._state_key, self._token_key = derive_subkeys(root)
        self._public_url = (
            getattr(framework_ctx, "_public_url", None) or os.environ.get("PUBLIC_URL", "")
        ).rstrip("/")
        self._redirect_uri = f"{self._public_url}/oauth/callback"
        self._oauth = PerUserOAuth(
            server_url=self._url,
            server_id=self._server_id,
            agent_name=framework_ctx.agent_name,
            storage=framework_ctx.storage,
            pending_flows=framework_ctx.pending_flows,
            slack_client=framework_ctx.slack_client,
            state_key=self._state_key,
            token_key=self._token_key,
            redirect_uri=self._redirect_uri,
            public_url=self._public_url,
            auth_timeout=self._auth_timeout,
            discovery=PrmDiscovery(resource_url=self._url),
        )
        # Tools are discovered eagerly via ensure_authenticated() when the user first
        # talks to the agent (or after restart, using the cached token). Until then
        # this stays empty.
        self._tool_initialized = False
        self._all_tools: list[dict] = []

    @staticmethod
    def _load_root_key() -> bytes:
        env = os.environ.get("OAUTH_SECRET_KEY")
        if env:
            return base64.b64decode(env, validate=True)
        import secrets as _secrets

        return _secrets.token_bytes(32)

    def _get_all_tools(self) -> list[dict]:
        return self._all_tools

    async def _discover_tools(self, user_conversation_context) -> list[str]:
        """Open an MCP session for this user and replace _all_tools with the real list.

        When no token is cached for this user, the SDK's 401-driven auth flow
        runs as part of `session.initialize()` — sending the auth ephemeral and
        awaiting the user's click. The first MCP call after auth populates the
        tool list.
        """
        user_id = user_conversation_context["user_id"]
        channel_id = user_conversation_context["channel_id"]
        thread_id = user_conversation_context.get("thread_id")
        oauth = await self._oauth.build_provider(user_id, channel_id, thread_id)
        async with httpx.AsyncClient(
            auth=oauth,
            timeout=httpx.Timeout(30.0, read=300.0),
            follow_redirects=True,
            event_hooks={"response": [self._oauth.auth_response_hook(user_id)]},
        ) as http_client:
            async with streamable_http_client(url=self._url, http_client=http_client) as (
                read,
                write,
                _get_session_id,
            ):
                async with mcp.ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self._all_tools = [
                        {
                            "name": t.name,
                            "description": t.description or "",
                            "input_schema": t.inputSchema or {"type": "object", "properties": {}},
                        }
                        for t in listed.tools
                    ]
                    self._tool_initialized = True
                    logger.info(
                        "oauth: %s tools discovered for %s: %d tools",
                        self._server_id,
                        user_id,
                        len(self._all_tools),
                    )
                    return [t["name"] for t in self._all_tools]

    async def initialize(self) -> None:
        """No startup work — discovery is per-user and happens via ensure_authenticated."""
        return None

    async def ensure_authenticated(self, user_conversation_context) -> None:
        """Pre-LLM hook: ensure the user has a token and tools are discovered.

        Called once per inbound message by the agent. Cheap when already
        authenticated (a single DB lookup). When no token exists, the SDK's
        auth flow runs inline during tool discovery — posting the auth-link
        ephemeral and awaiting the user's click for up to `auth_timeout`
        seconds. On success, the LLM sees the real tool list on this very turn.

        Raises:
            AuthSetupError: with a user-facing message when auth or discovery fails.
        """
        user_id = user_conversation_context["user_id"]
        token_storage = DBTokenStorage(
            backend=self._framework_ctx.storage,
            user_id=user_id,
            server_id=self._server_id,
            redirect_uri=self._redirect_uri,
            token_key=self._token_key,
        )
        had_token_before = await token_storage.get_tokens() is not None
        if not had_token_before or not self._tool_initialized:
            try:
                await self._discover_tools(user_conversation_context)
            except Exception as e:
                denied = find_user_authorization_denied(e)
                if denied is not None:
                    logger.info(
                        "oauth: user %s denied access to %s during initial auth "
                        "(code=%s, description=%s)",
                        user_id,
                        self._server_id,
                        denied.code,
                        denied.description,
                    )
                    raise AuthSetupError(
                        message_from_tool_error(user_denied_tool_result(self._server_id, denied))
                    ) from e
                action = (
                    "setting up authentication"
                    if not had_token_before
                    else "loading available tools"
                )
                raise AuthSetupError(
                    message_from_tool_error(
                        record_error(
                            server_id=self._server_id,
                            action_phrase=action,
                            exc=e,
                            user_id=user_id,
                        )
                    )
                ) from e
        if not had_token_before:
            await self._post_auth_success(user_conversation_context)

    async def _post_auth_success(self, user_conversation_context) -> None:
        """Post a brief Slack ephemeral confirming the user is now connected."""
        await self._oauth.log_user_info(user_conversation_context["user_id"])
        try:
            kwargs = {
                "channel": user_conversation_context["channel_id"],
                "user": user_conversation_context["user_id"],
                "text": (f"✅ Authenticated to *{self._server_id}*. Continuing your request..."),
            }
            if user_conversation_context.get("thread_id"):
                kwargs["thread_ts"] = user_conversation_context["thread_id"]
            await self._framework_ctx.slack_client.chat_postEphemeral(**kwargs)
        except Exception:
            logger.warning("oauth: failed to post auth-success message", exc_info=True)

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict,
        user_conversation_context,
        storage: BaseStorageProvider,
    ) -> ToolResult:
        """Run an MCP tool call. Auth, refresh, and any 401/403 step-up driven by
        the server's WWW-Authenticate header are handled by the SDK's
        `OAuthClientProvider` (attached as the httpx auth via `PerUserOAuth.build_provider`).
        """
        user_id = user_conversation_context["user_id"]
        try:
            return await self._call_mcp_with_token(user_conversation_context, tool_name, arguments)
        except Exception as e:
            # Specific recovery: IdP rejected our authorize request because the
            # client's registered redirect_uri doesn't match what we sent.
            # Means the cached `oauth_clients` row's redirect_uri is stale —
            # delete it so the next call re-registers, and surface a clear
            # message naming the cause.
            if is_redirect_uri_mismatch(e):
                await self._oauth.handle_redirect_uri_mismatch(user_id)
                return make_tool_error(
                    error=ERROR_SYSTEM_ERROR,
                    code="redirect_uri_mismatch",
                    server=self._server_id,
                    tool=tool_name,
                    recovery=RECOVERY_RETRY,
                    message=(
                        "The agent's PUBLIC_URL doesn't match the "
                        "redirect_uri registered with the OAuth client at "
                        "the IdP. The cached client registration has been "
                        "cleared — the next call will register a fresh "
                        "client and prompt for re-auth."
                    ),
                    details={"redirect_uri": self._redirect_uri},
                )
            denied = find_user_authorization_denied(e)
            if denied is None:
                # No IdP-level denial signaled. Did we get a final 403 from the
                # MCP server *after* the SDK already ran its step-up auth flow?
                # That means the new token still doesn't have the scope the
                # server is asking for — the user's account was permitted to
                # consent only to a subset, the IdP silently issued a token
                # without the missing scope, and the resource still says no.
                # That's a permission-denied case, not a system error.
                granted = await self._oauth.cached_scope_set(user_id)
                detection = detect_missing_scopes(e, granted)
                if detection is not None:
                    required, missing = detection
                    granted_resource = sorted(granted - OIDC_BASELINE_SCOPES)
                    denied = UserAuthorizationDenied(
                        code="scope_not_granted",
                        description=(
                            f"Server requires {sorted(required)}; "
                            f"user's account was granted {granted_resource}; "
                            f"missing: {sorted(missing)}"
                        ),
                        required_scopes=sorted(required),
                        granted_scopes=granted_resource,
                    )
            if denied is not None:
                logger.info(
                    "oauth: user %s denied access to %s (code=%s, description=%s)",
                    user_id,
                    self._server_id,
                    denied.code,
                    denied.description,
                )
                return user_denied_tool_result(self._server_id, denied, tool=tool_name)
            return record_error(
                server_id=self._server_id,
                action_phrase="completing the request",
                exc=e,
                tool=tool_name,
                user_id=user_id,
            )

    async def _call_mcp_with_token(
        self, user_conversation_context, tool_name: str, arguments: dict
    ) -> ToolResult:
        """Open an MCP session using the SDK's OAuthClientProvider as the httpx
        auth. The SDK handles refresh, full re-auth on refresh failure, and any
        scope step-up triggered by 401/403 + `WWW-Authenticate: Bearer
        error="insufficient_scope"`.
        """
        user_id = user_conversation_context["user_id"]
        channel_id = user_conversation_context["channel_id"]
        thread_id = user_conversation_context.get("thread_id")
        oauth = await self._oauth.build_provider(user_id, channel_id, thread_id)
        async with httpx.AsyncClient(
            auth=oauth,
            timeout=httpx.Timeout(30.0, read=300.0),
            follow_redirects=True,
            event_hooks={"response": [self._oauth.auth_response_hook(user_id)]},
        ) as http_client:
            async with streamable_http_client(url=self._url, http_client=http_client) as (
                read,
                write,
                _get_session_id,
            ):
                async with mcp.ClientSession(read, write) as session:
                    await session.initialize()
                    if not self._tool_initialized:
                        tools = await session.list_tools()
                        self._all_tools = [
                            {
                                "name": t.name,
                                "description": t.description or "",
                                "input_schema": t.inputSchema
                                or {"type": "object", "properties": {}},
                            }
                            for t in tools.tools
                        ]
                        self._tool_initialized = True
                    result = await session.call_tool(name=tool_name, arguments=arguments)
                    return _result_to_tool_result(result)


def _result_to_tool_result(result) -> ToolResult:
    text_parts: list[str] = []
    files: list[dict] = []
    for content in result.content:
        if isinstance(content, EmbeddedResource) and isinstance(
            content.resource, BlobResourceContents
        ):
            data = base64.b64decode(content.resource.blob)
            files.append(
                {
                    "data": data,
                    "filename": _uri_to_filename(content.resource.uri),
                    "mimeType": content.resource.mimeType or "application/octet-stream",
                }
            )
        elif isinstance(content, ImageContent):
            data = base64.b64decode(content.data)
            ext = content.mimeType.split("/")[-1] if content.mimeType else "png"
            files.append(
                {
                    "data": data,
                    "filename": f"image.{ext}",
                    "mimeType": content.mimeType,
                }
            )
        elif hasattr(content, "text"):
            text_parts.append(content.text)
        else:
            text_parts.append(str(content))
    return {
        "content": "\n".join(text_parts) if text_parts else "(empty result)",
        "is_error": bool(result.isError),
        "files": files,
    }
