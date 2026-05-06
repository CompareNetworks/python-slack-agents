"""DBTokenStorage — bridges mcp.client.auth.TokenStorage to the agent's storage backend.

Tokens are keyed by (user_id, server_id); client registration is keyed by server_id only
(one DCR per MCP server, shared across all users of that server in this agent).

Refresh tokens are AES-GCM-encrypted before insert and decrypted on read. If decryption
fails (e.g. OAUTH_SECRET_KEY rotated), the row is deleted and `get_tokens` returns None
so the SDK falls through to fresh auth.
"""

from __future__ import annotations

import logging
import time

from cryptography.exceptions import InvalidTag
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from slack_agents.oauth.crypto import decrypt_token, encrypt_token
from slack_agents.storage.base import (
    BaseStorageProvider,
    OAuthClientRow,
    OAuthTokenRow,
)

logger = logging.getLogger(__name__)


class DBTokenStorage:
    """Implements mcp.client.auth.TokenStorage for one (user_id, server_id) pair.

    Multiple instances may share the same `backend` and `token_key`; each is bound
    to a particular user/server.
    """

    def __init__(
        self,
        *,
        backend: BaseStorageProvider,
        user_id: str,
        server_id: str,
        token_key: bytes,
    ) -> None:
        self._backend = backend
        self._user_id = user_id
        self._server_id = server_id
        self._token_key = token_key

    async def get_tokens(self) -> OAuthToken | None:
        row = await self._backend.get_oauth_token(self._user_id, self._server_id)
        if row is None:
            return None
        refresh = None
        if row.refresh_token_enc is not None:
            try:
                refresh = decrypt_token(row.refresh_token_enc, self._token_key).decode()
            except InvalidTag:
                logger.warning(
                    "oauth: token decryption failed (key rotated?), forcing re-auth "
                    "(user=%s server=%s)",
                    self._user_id,
                    self._server_id,
                )
                await self._backend.delete_oauth_token(self._user_id, self._server_id)
                return None
        expires_in = None
        if row.expires_at is not None:
            # Return the actual remaining seconds (which may be negative for an
            # expired token). The SDK's `update_token_expiry` does
            # `time.time() + expires_in`, so this preserves the original absolute
            # expiry — which the SDK's `is_token_valid()` then checks against
            # current time. Clamping to 0 would mask the expiry and force the SDK
            # to treat the token as still valid for an instant.
            expires_in = row.expires_at - int(time.time())
        return OAuthToken(
            access_token=row.access_token,
            token_type=row.token_type,
            expires_in=expires_in,
            refresh_token=refresh,
            scope=row.scopes or None,
        )

    async def set_tokens(self, tokens: OAuthToken) -> None:
        now = int(time.time())
        expires_at = None
        if tokens.expires_in is not None:
            expires_at = now + int(tokens.expires_in)
        refresh_enc: str | None = None
        if tokens.refresh_token:
            refresh_enc = encrypt_token(tokens.refresh_token.encode(), self._token_key)
        existing = await self._backend.get_oauth_token(self._user_id, self._server_id)
        created_at = existing.created_at if existing else now
        row = OAuthTokenRow(
            access_token=tokens.access_token,
            refresh_token_enc=refresh_enc,
            token_type=tokens.token_type or "Bearer",
            scopes=tokens.scope or "",
            expires_at=expires_at,
            created_at=created_at,
            updated_at=now,
        )
        await self._backend.put_oauth_token(self._user_id, self._server_id, row)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        row = await self._backend.get_oauth_client(self._server_id)
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate_json(row.metadata_json)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        now = int(time.time())
        existing = await self._backend.get_oauth_client(self._server_id)
        created_at = existing.created_at if existing else now
        row = OAuthClientRow(
            client_id=client_info.client_id,
            client_secret=client_info.client_secret,
            metadata_json=client_info.model_dump_json(),
            authorization_server="",  # filled by Provider once known
            created_at=created_at,
            updated_at=now,
        )
        await self._backend.put_oauth_client(self._server_id, row)
