"""Per-user OAuth flow driver, reusable across MCP and A2A.

Builds a per-(user, server) `OAuthClientProvider` (an httpx.Auth) and the Slack
redirect/callback handlers, given a pluggable DiscoveryStrategy. Extracted from
tools/mcp_http_oauth.py; behavior is unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx
from mcp.client.auth import OAuthClientProvider
from mcp.client.auth.exceptions import OAuthFlowError
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata

from slack_agents import OAuthCallbackResult, PendingFlowsRegistry
from slack_agents.oauth.discovery import DiscoveryResult, DiscoveryStrategy
from slack_agents.oauth.errors import ReauthRequired, UserAuthorizationDenied
from slack_agents.oauth.prompts import AuthPromptDeliveryError, send_auth_prompt
from slack_agents.oauth.scopes import (
    OIDC_BASELINE_SCOPES,
    parse_www_auth_scope,
    replace_www_auth_scope,
)
from slack_agents.oauth.state import StatePayload
from slack_agents.oauth.state import encode as encode_state
from slack_agents.oauth.storage import DBTokenStorage

logger = logging.getLogger(__name__)


@dataclass
class PerUserHandle:
    """Per-(user, server) OAuth flow handle wired into the SDK's redirect/callback."""

    user_id: str
    channel_id: str
    thread_id: str | None
    server_id: str
    pending_flows: PendingFlowsRegistry
    slack_client: object
    state_key: bytes
    auth_timeout: int
    public_url: str
    interactive: bool = True
    _sdk_state: str | None = None
    _pending_future: "asyncio.Future[OAuthCallbackResult] | None" = None

    async def redirect_handler(self, authorize_url: str) -> None:
        if not self.interactive:
            raise ReauthRequired(
                f"re-authentication required for {self.server_id} but no interactive "
                f"session is available (background task)"
            )
        qs = parse_qs(urlparse(authorize_url).query)
        sdk_state_list = qs.get("state", [])
        if not sdk_state_list:
            raise OAuthFlowError("authorize URL is missing state parameter")
        self._sdk_state = sdk_state_list[0]
        self._pending_future = self.pending_flows.register(self._sdk_state)
        signed = encode_state(
            StatePayload(
                user_id=self.user_id,
                server_id=self.server_id,
                authorize_url=authorize_url,
                exp=int(time.time()) + self.auth_timeout,
            ),
            self.state_key,
        )
        try:
            await send_auth_prompt(
                slack_client=self.slack_client,
                user_id=self.user_id,
                channel_id=self.channel_id,
                thread_id=self.thread_id,
                server_name=self.server_id,
                signed_state=signed,
                public_url=self.public_url,
            )
        except AuthPromptDeliveryError:
            self.pending_flows.discard(self._sdk_state)
            raise

    async def callback_handler(self) -> tuple[str, str | None]:
        if self._pending_future is None:
            raise OAuthFlowError("callback_handler called before redirect_handler")
        result: OAuthCallbackResult = await asyncio.wait_for(
            self._pending_future, timeout=self.auth_timeout
        )
        if result.error or result.code is None:
            if result.error in UserAuthorizationDenied.USER_LEVEL_CODES:
                raise UserAuthorizationDenied(
                    code=result.error or "", description=result.error_description
                )
            raise OAuthFlowError(result.error_description or result.error or "authorization failed")
        return result.code, result.state


class PerUserOAuth:
    """Builds per-user OAuthClientProvider instances for one server.

    Owns the per-server DiscoveryResult cache (was _cached_prm/_cached_asm) and
    the DCR pre-registration. One instance per provider; build_provider() is
    called per user.
    """

    def __init__(
        self,
        *,
        server_url: str,
        server_id: str,
        agent_name: str,
        storage,
        pending_flows: PendingFlowsRegistry,
        slack_client,
        state_key: bytes,
        token_key: bytes,
        redirect_uri: str,
        public_url: str,
        auth_timeout: int,
        discovery: DiscoveryStrategy,
    ) -> None:
        self._server_url = server_url
        self._server_id = server_id
        self._agent_name = agent_name
        self._storage = storage
        self._pending_flows = pending_flows
        self._slack_client = slack_client
        self._state_key = state_key
        self._token_key = token_key
        self._redirect_uri = redirect_uri
        self._public_url = public_url
        self._auth_timeout = auth_timeout
        self._discovery = discovery
        self._cached: DiscoveryResult | None = None

    def make_handle(self, user_id, channel_id, thread_id, *, interactive=True) -> PerUserHandle:
        return PerUserHandle(
            user_id=user_id,
            channel_id=channel_id,
            thread_id=thread_id,
            server_id=self._server_id,
            pending_flows=self._pending_flows,
            slack_client=self._slack_client,
            state_key=self._state_key,
            auth_timeout=self._auth_timeout,
            public_url=self._public_url,
            interactive=interactive,
        )

    def _token_storage(self, user_id: str) -> DBTokenStorage:
        return DBTokenStorage(
            backend=self._storage,
            user_id=user_id,
            server_id=self._server_id,
            redirect_uri=self._redirect_uri,
            token_key=self._token_key,
        )

    async def _ensure_discovered(self) -> None:
        if self._cached is not None:
            return
        try:
            self._cached = await self._discovery.discover()
        except Exception:
            logger.warning(
                "oauth: discovery for %s failed; 401-driven SDK discovery will be used instead",
                self._server_id,
                exc_info=True,
            )

    async def build_provider(
        self, user_id, channel_id, thread_id, *, interactive=True
    ) -> OAuthClientProvider:
        handle = self.make_handle(user_id, channel_id, thread_id, interactive=interactive)
        token_storage = self._token_storage(user_id)
        await self._ensure_discovered()
        try:
            await self._ensure_client_registered()
        except Exception:
            logger.warning(
                "oauth: pre-DCR for %s failed; SDK will fall back to its own DCR",
                self._server_id,
                exc_info=True,
            )
        required = self._cached.required_scopes if self._cached else []
        seed_scope = None
        if required:
            seed_scope = " ".join(sorted(OIDC_BASELINE_SCOPES | set(required)))
        client_metadata = OAuthClientMetadata(
            redirect_uris=[self._redirect_uri],
            client_name=f"slack-agents/{self._agent_name}",
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=seed_scope,
        )
        oauth = OAuthClientProvider(
            server_url=self._server_url,
            client_metadata=client_metadata,
            storage=token_storage,
            redirect_handler=handle.redirect_handler,
            callback_handler=handle.callback_handler,
            timeout=self._auth_timeout,
        )
        self._apply_cached_metadata(oauth)
        try:
            await oauth._initialize()
            tokens = oauth.context.current_tokens
            if tokens and tokens.expires_in:
                oauth.context.update_token_expiry(tokens)
        except Exception:
            logger.warning("oauth: pre-initialize for %s failed", self._server_id, exc_info=True)
        return oauth

    def _apply_cached_metadata(self, oauth: OAuthClientProvider) -> None:
        if self._cached is None:
            return
        prm = self._cached.resource_metadata
        asm = self._cached.authorization_server_metadata
        if prm is not None:
            oauth.context.protected_resource_metadata = prm
            if prm.authorization_servers:
                oauth.context.auth_server_url = str(prm.authorization_servers[0])
        if asm is not None:
            oauth.context.oauth_metadata = asm

    async def _ensure_client_registered(self) -> None:
        if await self._storage.get_oauth_client(self._server_id, self._redirect_uri) is not None:
            return
        asm = self._cached.authorization_server_metadata if self._cached else None
        if asm is None or asm.registration_endpoint is None:
            return
        body: dict = {
            "redirect_uris": [self._redirect_uri],
            "client_name": f"slack-agents/{self._agent_name}",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        }
        # Register for the discovered catalog PLUS the OIDC baseline (openid,
        # offline_access). The client always requests the baseline at authorize, so
        # the registered client must be permitted it — some IdPs (strict DCR
        # policies, e.g. Keycloak "Allowed Client Scopes") only let a client request
        # the scopes it registered with, and otherwise reject offline_access/openid.
        catalog = self._cached.scope_catalog if self._cached else []
        dcr_scopes = sorted(set(catalog) | OIDC_BASELINE_SCOPES)
        if dcr_scopes:
            body["scope"] = " ".join(dcr_scopes)
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as http:
            resp = await http.post(str(asm.registration_endpoint), json=body)
            resp.raise_for_status()
            client_info = OAuthClientInformationFull.model_validate_json(resp.content)
        await DBTokenStorage(
            backend=self._storage,
            user_id="__system__",
            server_id=self._server_id,
            redirect_uri=self._redirect_uri,
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

    async def has_token(self, user_id: str) -> bool:
        """True if this user already has a stored token for the server."""
        try:
            return await self._token_storage(user_id).get_tokens() is not None
        except Exception:
            return False

    async def log_user_info(self, user_id: str) -> None:
        """Best-effort: fetch OIDC userinfo with the user's access token and log it.

        Requires the `openid`/`profile` scopes (in OIDC_BASELINE_SCOPES) and a
        `userinfo_endpoint` (captured during discovery). Never raises — identity
        logging must not break the request flow.
        """
        endpoint = self._cached.userinfo_endpoint if self._cached else None
        if not endpoint:
            return
        try:
            tokens = await self._token_storage(user_id).get_tokens()
            if not tokens or not tokens.access_token:
                return
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as http:
                resp = await http.get(
                    endpoint, headers={"Authorization": f"Bearer {tokens.access_token}"}
                )
            if resp.status_code != 200:
                logger.info(
                    "oauth: %s userinfo for %s returned HTTP %s",
                    self._server_id,
                    user_id,
                    resp.status_code,
                )
                return
            claims = resp.json()
            logger.info(
                "oauth: %s authenticated user %s — sub=%s preferred_username=%r name=%r email=%r",
                self._server_id,
                user_id,
                claims.get("sub"),
                claims.get("preferred_username"),
                claims.get("name"),
                claims.get("email"),
            )
        except Exception:
            logger.debug("oauth: %s userinfo fetch failed", self._server_id, exc_info=True)

    async def cached_scope_set(self, user_id: str) -> set[str]:
        try:
            cached = await self._token_storage(user_id).get_tokens()
            if cached and cached.scope:
                return set(cached.scope.split())
        except Exception:
            logger.debug("oauth: could not read cached scopes", exc_info=True)
        return set()

    def auth_response_hook(self, user_id: str):
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
            hint = parse_www_auth_scope(ww)
            if hint is None:
                return
            cached_scopes = await self.cached_scope_set(user_id)
            hint_scopes = set(hint.split())
            merged = sorted(OIDC_BASELINE_SCOPES | cached_scopes | hint_scopes)
            new_scope = " ".join(merged)
            if new_scope == hint:
                return
            response.headers["WWW-Authenticate"] = replace_www_auth_scope(ww, new_scope)
            logger.info(
                "oauth: scope merged for next authorize: %s (baseline=%s, cached=%s, hint=%s)",
                new_scope,
                sorted(OIDC_BASELINE_SCOPES),
                sorted(cached_scopes),
                sorted(hint_scopes),
            )

        return hook

    async def handle_redirect_uri_mismatch(self, user_id: str) -> None:
        await self._storage.delete_oauth_client(self._server_id, self._redirect_uri)
        await self._storage.delete_oauth_token(user_id, self._server_id)
        logger.warning(
            "oauth: cleared stale registration after IdP redirect_uri mismatch (server=%s)",
            self._server_id,
        )
