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

import asyncio
import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import mcp
from mcp.client.auth import OAuthClientProvider
from mcp.client.auth.exceptions import OAuthFlowError
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    ProtectedResourceMetadata,
)
from mcp.types import BlobResourceContents, EmbeddedResource, ImageContent

from slack_agents import FrameworkContext, OAuthCallbackResult
from slack_agents.oauth.crypto import derive_subkeys
from slack_agents.oauth.prompts import AuthPromptDeliveryError, send_auth_prompt
from slack_agents.oauth.state import StatePayload
from slack_agents.oauth.state import encode as encode_state
from slack_agents.oauth.storage import DBTokenStorage
from slack_agents.storage.base import BaseStorageProvider
from slack_agents.tools.base import (
    ERROR_PERMISSION_DENIED,
    ERROR_SYSTEM_ERROR,
    RECOVERY_CONTACT_ADMIN,
    RECOVERY_CONTACT_SUPPORT,
    RECOVERY_RETRY,
    BaseToolProvider,
    ToolResult,
    make_tool_error,
)
from slack_agents.tools.mcp_http import _uri_to_filename

logger = logging.getLogger(__name__)


_DEFAULT_AUTH_TIMEOUT = 300
_DEFAULT_INIT_RETRIES = [5, 10, 30]

# OIDC protocol scopes that the client always wants on top of any resource
# scopes a server signals. The MCP SDK uses the server's WWW-Authenticate
# `scope=` value verbatim as the next authorize request's scope, so without
# this baseline added on our side, restart-after-token-expiry loses
# `offline_access` (no refresh token) and identity-bearing flows lose `openid`.
_OIDC_BASELINE_SCOPES = frozenset({"openid", "offline_access"})

# Parser for the `scope="…"` parameter in a `WWW-Authenticate: Bearer …` header.
# RFC 7235 challenge syntax allows other quoted/unquoted parameters, but the
# MCP servers we target follow the common form: `key="value", key="value", …`.
_WWW_AUTH_SCOPE_RE = re.compile(r'scope\s*=\s*"([^"]*)"')


def _parse_www_auth_scope(header: str) -> str | None:
    """Return the `scope=` value from a WWW-Authenticate header, or None."""
    m = _WWW_AUTH_SCOPE_RE.search(header)
    return m.group(1) if m else None


def _replace_www_auth_scope(header: str, new_scope: str) -> str:
    """Return a copy of the header with its `scope=` value replaced."""
    return _WWW_AUTH_SCOPE_RE.sub(f'scope="{new_scope}"', header, count=1)


def _utc_timestamp() -> str:
    """ISO-8601 UTC timestamp used to correlate Slack messages with log lines."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _short_exception(exc: BaseException) -> str:
    """One-line summary of an exception, with HTML bodies and long messages trimmed."""
    msg = str(exc) or ""
    cut = msg.find("<")
    if cut != -1:
        msg = msg[:cut]
    msg = msg.replace("\n", " ").strip()
    if len(msg) > 200:
        msg = msg[:200].rstrip() + "…"
    name = type(exc).__name__
    return f"{name}: {msg}" if msg else name


def _record_error(
    *,
    server_id: str,
    action_phrase: str,
    exc: BaseException,
    tool: str | None = None,
    user_id: str | None = None,
) -> ToolResult:
    """Log the full traceback with a UTC timestamp tag, return a `make_tool_error`
    result (system error, contact_support).

    The timestamp ends up in `details.timestamp_utc` and in the log line — the
    operator can grep for it.
    """
    ts = _utc_timestamp()
    logger.exception(
        "oauth: failure on server=%s user=%s ts=%s action=%s",
        server_id,
        user_id or "?",
        ts,
        action_phrase,
    )
    return make_tool_error(
        error=ERROR_SYSTEM_ERROR,
        recovery=RECOVERY_CONTACT_SUPPORT,
        server=server_id,
        tool=tool,
        message=(
            f"{server_id} could not complete the request while {action_phrase}. "
            "An operator should investigate the agent logs."
        ),
        details={
            "action": action_phrase,
            "exception": _short_exception(exc),
            "timestamp_utc": ts,
        },
    )


class AuthSetupError(Exception):
    """Raised by ensure_authenticated() — user-facing message describes the failure."""


class UserAuthorizationDenied(Exception):
    """Raised when the upstream IdP (or the resource server) rejects access
    because the user's account lacks permission for the operation. Distinct
    from `OAuthFlowError` so the caller can surface a user-level message.

    Codes can come from two layers:
      - IdP-level: "invalid_scope", "access_denied", "consent_required",
        "login_required" — set by the OAuth callback when the IdP returns an
        error parameter.
      - Resource-level: "scope_not_granted" — set by us when the user's
        post-auth token still doesn't have the scope the resource server is
        demanding (e.g. user consented but Keycloak silently dropped a scope
        their role doesn't grant).

    Attributes:
        code: classification of the denial.
        description: human-readable detail.
        required_scopes / granted_scopes: when set, used to produce a clearer
            user-facing message that names the specific scopes involved.
    """

    USER_LEVEL_CODES = frozenset(
        {
            "invalid_scope",
            "access_denied",
            "consent_required",
            "login_required",
            "scope_not_granted",
        }
    )

    def __init__(
        self,
        code: str,
        description: str | None = None,
        *,
        required_scopes: list[str] | None = None,
        granted_scopes: list[str] | None = None,
    ) -> None:
        self.code = code
        self.description = description
        self.required_scopes = required_scopes
        self.granted_scopes = granted_scopes
        super().__init__(description or code)


def _flatten_exceptions(exc: BaseException) -> list[BaseException]:
    """Walk an ExceptionGroup tree (and __cause__/__context__) and return all leaves."""
    out: list[BaseException] = []
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        e = stack.pop()
        if id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, BaseExceptionGroup):
            stack.extend(e.exceptions)
        else:
            out.append(e)
        if e.__cause__ is not None:
            stack.append(e.__cause__)
        if e.__context__ is not None and e.__cause__ is None:
            stack.append(e.__context__)
    return out


def _find_user_authorization_denied(exc: BaseException) -> UserAuthorizationDenied | None:
    """Look for a UserAuthorizationDenied anywhere in the exception chain."""
    for leaf in _flatten_exceptions(exc):
        if isinstance(leaf, UserAuthorizationDenied):
            return leaf
    return None


@dataclass
class _PerUserHandle:
    """Per-(user, server) OAuth flow handle wired into the SDK."""

    provider: "Provider"
    user_id: str
    channel_id: str
    thread_id: str | None
    server_id: str
    _sdk_state: str | None = None
    # Save the Future returned by register() directly. The registry pops on
    # resolve(), so keying back into _flows after resolution returns None —
    # holding the Future avoids that race.
    _pending_future: "asyncio.Future[OAuthCallbackResult] | None" = None

    async def redirect_handler(self, authorize_url: str) -> None:
        qs = parse_qs(urlparse(authorize_url).query)
        sdk_state_list = qs.get("state", [])
        if not sdk_state_list:
            raise OAuthFlowError("authorize URL is missing state parameter")
        self._sdk_state = sdk_state_list[0]
        self._pending_future = self.provider._framework_ctx.pending_flows.register(self._sdk_state)
        signed = encode_state(
            StatePayload(
                user_id=self.user_id,
                server_id=self.server_id,
                authorize_url=authorize_url,
                exp=int(time.time()) + self.provider._auth_timeout,
            ),
            self.provider._state_key,
        )
        try:
            await send_auth_prompt(
                slack_client=self.provider._framework_ctx.slack_client,
                user_id=self.user_id,
                channel_id=self.channel_id,
                thread_id=self.thread_id,
                server_name=self.server_id,
                signed_state=signed,
                public_url=self.provider._public_url,
            )
        except AuthPromptDeliveryError:
            self.provider._framework_ctx.pending_flows.discard(self._sdk_state)
            raise

    async def callback_handler(self) -> tuple[str, str | None]:
        if self._pending_future is None:
            raise OAuthFlowError("callback_handler called before redirect_handler")
        result: OAuthCallbackResult = await asyncio.wait_for(
            self._pending_future, timeout=self.provider._auth_timeout
        )
        if result.error or result.code is None:
            # Distinguish user-level rejections (no permission, denied consent,
            # etc.) from system-level OAuth errors so the caller can surface a
            # different message. The IdP's error code drives the classification.
            if result.error in UserAuthorizationDenied.USER_LEVEL_CODES:
                raise UserAuthorizationDenied(
                    code=result.error or "",
                    description=result.error_description,
                )
            raise OAuthFlowError(result.error_description or result.error or "authorization failed")
        return result.code, result.state


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
            getattr(framework_ctx, "_public_url", None) or os.environ.get("OAUTH_PUBLIC_URL", "")
        ).rstrip("/")
        # Tools are discovered eagerly via ensure_authenticated() when the user first
        # talks to the agent (or after restart, using the cached token). Until then
        # this stays empty.
        self._tool_initialized = False
        self._all_tools: list[dict] = []
        # Per-server metadata cache. Once we've fetched protected-resource and
        # authorization-server metadata for this server, we reuse the result for
        # every subsequent OAuthClientProvider we build, both to avoid redundant
        # HTTP requests and to make the SDK's 403 step-up path work (see
        # `_populate_oauth_metadata` for why).
        self._cached_prm: ProtectedResourceMetadata | None = None
        self._cached_asm: OAuthMetadata | None = None

    @staticmethod
    def _load_root_key() -> bytes:
        env = os.environ.get("OAUTH_SECRET_KEY")
        if env:
            return base64.b64decode(env, validate=True)
        import secrets as _secrets

        return _secrets.token_bytes(32)

    def _oauth_handle_for_user(
        self, user_id: str, channel_id: str, thread_id: str | None
    ) -> _PerUserHandle:
        return _PerUserHandle(
            provider=self,
            user_id=user_id,
            channel_id=channel_id,
            thread_id=thread_id,
            server_id=self._server_id,
        )

    def _get_all_tools(self) -> list[dict]:
        return self._all_tools

    async def _build_oauth_for_user(
        self,
        user_id: str,
        channel_id: str,
        thread_id: str | None,
    ) -> OAuthClientProvider:
        """Build a per-user OAuthClientProvider attached as httpx auth on every
        MCP request. Refresh-token use, full re-auth on refresh failure, and DCR
        on first run are all handled by the SDK once this is wired up.

        We pre-populate `oauth.context.oauth_metadata` and
        `oauth.context.protected_resource_metadata` (cached on the Provider) to
        work around an MCP-SDK bug: the SDK's 403 step-up path
        (`oauth2.py::async_auth_flow`) calls `_perform_authorization()` without
        first running protected-resource discovery. On a freshly constructed
        provider that hasn't seen a 401, that leaves `oauth_metadata` as None,
        and the SDK falls back to `urljoin(server_url, "/authorize")` — which
        is wrong when the AS is on a different host than the resource server.
        """
        handle = self._oauth_handle_for_user(user_id, channel_id, thread_id)
        token_storage = DBTokenStorage(
            backend=self._framework_ctx.storage,
            user_id=user_id,
            server_id=self._server_id,
            token_key=self._token_key,
        )
        # Make sure the metadata cache is populated FIRST. We need it for the
        # pre-DCR registration (below) and for the SDK context pre-populate.
        try:
            await self._ensure_metadata_cached()
        except Exception:
            logger.warning(
                "oauth: pre-fetch of authorization metadata for %s failed; "
                "401-driven SDK discovery will be used instead",
                self._server_id,
                exc_info=True,
            )
        # Pre-register the OAuth client ourselves with PRM's full scope set.
        # The SDK's `async_auth_flow` overwrites `client_metadata.scope` with
        # the runtime authorize scope (priority: WWW-Authenticate hint > PRM)
        # *before* it runs DCR — meaning if we let the SDK do DCR, the
        # registered client only gets the scopes for the current operation,
        # never the broader catalog the resource advertises. We do DCR first,
        # persist the result via DBTokenStorage, and the SDK then sees an
        # already-registered client and skips its own DCR step entirely.
        try:
            await self._ensure_client_registered()
        except Exception:
            logger.warning(
                "oauth: pre-DCR for %s failed; SDK will fall back to its own "
                "DCR (which may register with a narrower scope set)",
                self._server_id,
                exc_info=True,
            )
        client_metadata = OAuthClientMetadata(
            redirect_uris=[f"{self._public_url}/oauth/callback"],
            client_name=f"slack-agents/{self._framework_ctx.agent_name}",
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        )
        oauth = OAuthClientProvider(
            server_url=self._url,
            client_metadata=client_metadata,
            storage=token_storage,
            redirect_handler=handle.redirect_handler,
            callback_handler=handle.callback_handler,
            timeout=self._auth_timeout,
        )
        # Apply cached metadata onto the SDK's context — works around an
        # MCP-SDK bug where the 403 step-up path
        # (`oauth2.py::async_auth_flow`) calls `_perform_authorization()`
        # without first running protected-resource discovery, leaving
        # `oauth_metadata` as None and falling back to `urljoin(server_url,
        # "/authorize")` (wrong when the AS is on a different host).
        self._apply_cached_metadata(oauth)
        # Force the SDK to load tokens + client_info from storage immediately,
        # then propagate the expiry so is_token_valid() can tell whether the
        # cached access token is actually still good. The SDK's `_initialize()`
        # only populates `current_tokens`/`client_info` and leaves
        # `token_expiry_time` at None — which makes `is_token_valid()` return
        # True for *any* loaded token, even expired ones, and skips the
        # refresh-token branch in async_auth_flow.
        try:
            await oauth._initialize()
            tokens = oauth.context.current_tokens
            if tokens and tokens.expires_in:
                oauth.context.update_token_expiry(tokens)
        except Exception:
            logger.warning(
                "oauth: pre-initialize for %s failed; "
                "the SDK will fall back to lazy initialization",
                self._server_id,
                exc_info=True,
            )
        return oauth

    async def _ensure_client_registered(self) -> None:
        """Pre-register an OAuth client for this server with PRM's full scope
        set, persisting the result so the SDK skips its own (narrower) DCR.

        Why this is needed: the MCP SDK's `async_auth_flow` calls
        `get_client_metadata_scopes(...)` and assigns the result to
        `self.context.client_metadata.scope` *before* running DCR. That scope is
        the authorize-time scope (driven by WWW-Authenticate hints / PRM), not
        the catalog of scopes the client should be eligible to ever request.
        So if the SDK does DCR, the registered client gets a registration scope
        equal to "what's needed for the current operation," not the broader set
        a step-up flow would later need to invoke.

        We work around it by doing DCR ourselves first, with PRM's full
        `scopes_supported`, and storing the result. The SDK loads it via
        `_initialize` and the `if not self.context.client_info: ... DCR` branch
        is skipped.

        Idempotent — does nothing if a client is already cached for this server.
        """
        backend = self._framework_ctx.storage
        if await backend.get_oauth_client(self._server_id) is not None:
            return
        if self._cached_asm is None or self._cached_asm.registration_endpoint is None:
            return
        body: dict = {
            "redirect_uris": [f"{self._public_url}/oauth/callback"],
            "client_name": f"slack-agents/{self._framework_ctx.agent_name}",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        }
        dcr_scopes = self._derive_dcr_scopes()
        if dcr_scopes:
            body["scope"] = " ".join(dcr_scopes)
        registration_url = str(self._cached_asm.registration_endpoint)
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as http:
            resp = await http.post(registration_url, json=body)
            resp.raise_for_status()
            client_info = OAuthClientInformationFull.model_validate_json(resp.content)
        # Persist via DBTokenStorage. Client info is keyed only by server_id
        # so the user_id passed here doesn't matter for this code path.
        await DBTokenStorage(
            backend=backend,
            user_id="__system__",
            server_id=self._server_id,
            token_key=self._token_key,
        ).set_client_info(client_info)
        registered_scope = getattr(client_info, "scope", None) or "(none)"
        logger.info(
            "oauth: %s pre-registered client (client_id=%s) — "
            "requested scope=%s, registered scope=%s",
            self._server_id,
            client_info.client_id,
            body.get("scope") or "(none)",
            registered_scope,
        )

    def _derive_dcr_scopes(self) -> list[str]:
        """Scopes to declare on the DCR registration request.

        Strictly mirrors what the protected-resource server advertises in its
        PRM `scopes_supported`. The agent does NOT guess scope names from
        conventions like `:read` → `:write` — that's brittle (servers don't
        agree on naming) and over-permissive. If a tool the LLM later calls
        requires a scope the client wasn't registered for, the failure is
        explicit and points to the right place: the resource server's PRM
        needs to advertise that scope for DCR clients to register for it.
        """
        if self._cached_prm and self._cached_prm.scopes_supported:
            return sorted(self._cached_prm.scopes_supported)
        return []

    def _apply_cached_metadata(self, oauth: OAuthClientProvider) -> None:
        """Copy the per-Provider cached PRM and AS metadata onto the SDK's context."""
        if self._cached_prm is not None:
            oauth.context.protected_resource_metadata = self._cached_prm
            if self._cached_prm.authorization_servers:
                oauth.context.auth_server_url = str(self._cached_prm.authorization_servers[0])
        if self._cached_asm is not None:
            oauth.context.oauth_metadata = self._cached_asm

    async def _ensure_metadata_cached(self) -> None:
        """Fetch protected-resource and authorization-server metadata into the
        per-Provider cache, if not already there. Idempotent; subsequent calls
        for the same server are no-ops.
        """
        if self._cached_prm is not None and self._cached_asm is not None:
            return

        parsed = urlparse(self._url)
        resource_root = f"{parsed.scheme}://{parsed.netloc}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), follow_redirects=True) as http:
            if self._cached_prm is None:
                prm_resp = await http.get(f"{resource_root}/.well-known/oauth-protected-resource")
                prm_resp.raise_for_status()
                self._cached_prm = ProtectedResourceMetadata.model_validate_json(prm_resp.content)
            if self._cached_asm is None and self._cached_prm.authorization_servers:
                auth_server_url = str(self._cached_prm.authorization_servers[0])
                # Try the OAuth 2.0 (RFC 8414) and OIDC discovery URL shapes —
                # Keycloak responds at .../oauth-authorization-server/realms/<r>
                # while the path-suffix form .../<server-path>/.well-known/...
                # also works on some servers. Fall back through the variants.
                as_parsed = urlparse(auth_server_url)
                candidates = []
                if as_parsed.path and as_parsed.path != "/":
                    base = f"{as_parsed.scheme}://{as_parsed.netloc}"
                    candidates.append(
                        f"{base}/.well-known/oauth-authorization-server{as_parsed.path}"
                    )
                candidates.append(
                    f"{auth_server_url.rstrip('/')}/.well-known/oauth-authorization-server"
                )
                for url in candidates:
                    try:
                        as_resp = await http.get(url)
                        if as_resp.status_code == 200:
                            self._cached_asm = OAuthMetadata.model_validate_json(as_resp.content)
                            logger.debug(
                                "oauth: cached AS metadata for %s from %s",
                                self._server_id,
                                url,
                            )
                            return
                    except httpx.HTTPError:
                        continue
                logger.warning(
                    "oauth: could not fetch AS metadata for %s from any of %s",
                    self._server_id,
                    candidates,
                )

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
        oauth = await self._build_oauth_for_user(user_id, channel_id, thread_id)
        async with httpx.AsyncClient(
            auth=oauth,
            timeout=httpx.Timeout(30.0, read=300.0),
            follow_redirects=True,
            event_hooks={"response": [self._make_auth_response_hook(user_id)]},
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
            token_key=self._token_key,
        )
        had_token_before = await token_storage.get_tokens() is not None
        if not had_token_before or not self._tool_initialized:
            try:
                await self._discover_tools(user_conversation_context)
            except Exception as e:
                denied = _find_user_authorization_denied(e)
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
                        _message_from_tool_error(_user_denied_tool_result(self._server_id, denied))
                    ) from e
                action = (
                    "setting up authentication"
                    if not had_token_before
                    else "loading available tools"
                )
                raise AuthSetupError(
                    _message_from_tool_error(
                        _record_error(
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
        `OAuthClientProvider` (attached as the httpx auth in `_build_oauth_for_user`).
        """
        user_id = user_conversation_context["user_id"]
        try:
            return await self._call_mcp_with_token(user_conversation_context, tool_name, arguments)
        except Exception as e:
            denied = _find_user_authorization_denied(e)
            if denied is None:
                # No IdP-level denial signaled. Did we get a final 403 from the
                # MCP server *after* the SDK already ran its step-up auth flow?
                # That means the new token still doesn't have the scope the
                # server is asking for — the user's account was permitted to
                # consent only to a subset, the IdP silently issued a token
                # without the missing scope, and the resource still says no.
                # That's a permission-denied case, not a system error.
                granted = await self._cached_scope_set(user_id)
                detection = _detect_missing_scopes(e, granted)
                if detection is not None:
                    required, missing = detection
                    granted_resource = sorted(granted - _OIDC_BASELINE_SCOPES)
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
                return _user_denied_tool_result(self._server_id, denied, tool=tool_name)
            return _record_error(
                server_id=self._server_id,
                action_phrase="completing the request",
                exc=e,
                tool=tool_name,
                user_id=user_id,
            )

    async def _cached_scope_set(self, user_id: str) -> set[str]:
        """Return the set of scopes on the user's currently-cached token, or
        an empty set if there's no cached token. Used to detect when a 403
        from the MCP server represents "user lacks the requested scope" vs.
        "system error."
        """
        try:
            cached = await DBTokenStorage(
                backend=self._framework_ctx.storage,
                user_id=user_id,
                server_id=self._server_id,
                token_key=self._token_key,
            ).get_tokens()
            if cached and cached.scope:
                return set(cached.scope.split())
        except Exception:
            logger.debug("oauth: could not read cached scopes", exc_info=True)
        return set()

    def _make_auth_response_hook(self, user_id: str):
        """Build an httpx response hook that, on 401/403 with a `WWW-Authenticate
        scope="…"` challenge from the MCP server, rewrites the scope value to be
        the union of:

          - the OIDC protocol baseline (openid, offline_access)
          - whatever scopes the user's currently-cached token already has
          - the scope set the server just signaled

        The MCP SDK's `get_client_metadata_scopes` then reads this augmented
        value verbatim (priority 1) and uses it as the next authorize request's
        scope. This implements the "client maintains scope state, server is
        stateless" architecture the MCP SDK doesn't quite provide on its own:

          - Server can return a delta (`scope="mcp:test:write"`) and the client
            merges with what it already has.
          - Server can return the cumulative set and the merge is a no-op for
            the resource scopes — but always adds the OIDC baseline.

        Bound to `user_id` via closure so the hook can fetch the cached
        token's scopes for that user without needing a ContextVar.
        """

        async def hook(response: httpx.Response) -> None:
            if response.status_code not in (401, 403):
                return
            ww = response.headers.get("WWW-Authenticate")
            if not ww:
                return
            logger.info(
                "oauth: %s from %s — WWW-Authenticate: %s",
                response.status_code,
                response.url,
                ww,
            )
            hint = _parse_www_auth_scope(ww)
            if hint is None:
                # No scope= parameter to augment. SDK falls back to PRM
                # scopes_supported (priority 2), which already includes the
                # OIDC baseline if PRM is well-formed.
                return
            cached_scopes: set[str] = set()
            try:
                cached = await DBTokenStorage(
                    backend=self._framework_ctx.storage,
                    user_id=user_id,
                    server_id=self._server_id,
                    token_key=self._token_key,
                ).get_tokens()
                if cached and cached.scope:
                    cached_scopes = set(cached.scope.split())
            except Exception:
                # If we can't read the cache, baseline + hint is still safer
                # than the bare hint.
                logger.debug(
                    "oauth: could not read cached token scopes for merge",
                    exc_info=True,
                )
            hint_scopes = set(hint.split())
            merged = sorted(_OIDC_BASELINE_SCOPES | cached_scopes | hint_scopes)
            new_scope = " ".join(merged)
            if new_scope == hint:
                return
            response.headers["WWW-Authenticate"] = _replace_www_auth_scope(ww, new_scope)
            logger.info(
                "oauth: scope merged for next authorize: %s (baseline=%s, cached=%s, hint=%s)",
                new_scope,
                sorted(_OIDC_BASELINE_SCOPES),
                sorted(cached_scopes),
                sorted(hint_scopes),
            )

        return hook

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
        oauth = await self._build_oauth_for_user(user_id, channel_id, thread_id)
        async with httpx.AsyncClient(
            auth=oauth,
            timeout=httpx.Timeout(30.0, read=300.0),
            follow_redirects=True,
            event_hooks={"response": [self._make_auth_response_hook(user_id)]},
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


# Per-code reason text + recovery action for user-level auth denials.
# The recovery action drives what the LLM/UI should advise the user to do next.
_USER_DENIED: dict[str, tuple[str, str]] = {
    # code: (reason text, recovery action)
    "invalid_scope": (
        "your account does not have a role granting the required scope",
        RECOVERY_CONTACT_ADMIN,
    ),
    "scope_not_granted": (
        "your account does not have a role granting the required scope; "
        "the authorization server issued a token without it",
        RECOVERY_CONTACT_ADMIN,
    ),
    "access_denied": (
        "access was denied at the authorization server",
        RECOVERY_RETRY,
    ),
    "consent_required": (
        "consent is required and was not granted",
        RECOVERY_RETRY,
    ),
    "login_required": (
        "the authorization server requires re-authentication",
        RECOVERY_RETRY,
    ),
}


def _user_denied_tool_result(
    server_id: str,
    denied: "UserAuthorizationDenied",
    tool: str | None = None,
) -> ToolResult:
    """Build a `make_tool_error` ToolResult for a user-level authorization rejection."""
    reason, recovery = _USER_DENIED.get(
        denied.code,
        (f"the authorization server returned `{denied.code}`", RECOVERY_CONTACT_ADMIN),
    )
    details: dict[str, Any] = {}
    if denied.description and denied.description != denied.code:
        d = denied.description.replace("\n", " ").strip()
        if len(d) > 200:
            d = d[:200].rstrip() + "…"
        details["description"] = d
    if denied.required_scopes is not None:
        details["required_scopes"] = denied.required_scopes
    if denied.granted_scopes is not None:
        details["granted_scopes"] = denied.granted_scopes
    if denied.required_scopes is not None and denied.granted_scopes is not None:
        details["missing_scopes"] = sorted(set(denied.required_scopes) - set(denied.granted_scopes))
    return make_tool_error(
        error=ERROR_PERMISSION_DENIED,
        code=denied.code,
        server=server_id,
        tool=tool,
        recovery=recovery,
        message=(
            f"This action cannot be run on {server_id}: {reason}. "
            "Ask whoever administers your account to grant the required access, "
            "then try again."
        ),
        details=details or None,
    )


def _www_authenticate_to_required_scopes(www_auth: str | None) -> set[str]:
    """Extract the resource scopes the server is asking for from a 401/403.

    Strips the OIDC plumbing scopes (`openid`, `offline_access`) so the result
    only contains scopes that meaningfully affect authorization decisions.
    Returns an empty set if no scope= parameter was present.
    """
    if not www_auth:
        return set()
    raw = _parse_www_auth_scope(www_auth)
    if not raw:
        return set()
    return set(raw.split()) - _OIDC_BASELINE_SCOPES


def _detect_missing_scopes(
    exc: BaseException, granted_scopes: set[str]
) -> tuple[set[str], set[str]] | None:
    """Walk the exception chain. If a 403 from the MCP server names required
    scopes and the user's currently-granted token is missing one or more of
    them, return (required_scopes, missing_scopes). Otherwise return None.

    "Missing" is computed against the resource scopes only — protocol scopes
    like openid and offline_access don't count toward authorization decisions.
    """
    for leaf in _flatten_exceptions(exc):
        if not isinstance(leaf, httpx.HTTPStatusError):
            continue
        if leaf.response.status_code != 403:
            continue
        required = _www_authenticate_to_required_scopes(
            leaf.response.headers.get("WWW-Authenticate")
        )
        if not required:
            continue
        granted_resource = granted_scopes - _OIDC_BASELINE_SCOPES
        missing = required - granted_resource
        if missing:
            return required, missing
    return None


def _message_from_tool_error(tr: ToolResult) -> str:
    """Extract the human-readable `message` field from a structured tool-error
    ToolResult, for paths (like ensure_authenticated) that surface errors
    directly to Slack rather than to an LLM. Falls back to the raw content if
    the payload isn't well-formed JSON.
    """
    try:
        payload = json.loads(tr["content"])
    except (json.JSONDecodeError, TypeError):
        return tr["content"]
    return payload.get("message") or tr["content"]


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
