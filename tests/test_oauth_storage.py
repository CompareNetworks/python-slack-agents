"""Tests for DBTokenStorage — bridges mcp.client.auth.TokenStorage to BaseStorageProvider."""

import secrets

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from slack_agents.oauth.crypto import derive_subkeys
from slack_agents.oauth.storage import DBTokenStorage
from slack_agents.storage.sqlite import Provider as SqliteProvider


@pytest.fixture
async def storage_backend():
    s = SqliteProvider(path=":memory:")
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
def token_key():
    _, k = derive_subkeys(secrets.token_bytes(32))
    return k


class TestSetAndGetTokens:
    async def test_roundtrip(self, storage_backend, token_key):
        store = DBTokenStorage(
            backend=storage_backend,
            user_id="U1",
            server_id="srv",
            redirect_uri="https://agent.example.com/oauth/callback",
            token_key=token_key,
        )
        token = OAuthToken(
            access_token="at",
            token_type="Bearer",
            expires_in=3600,
            refresh_token="rt",
            scope="read:docs",
        )
        await store.set_tokens(token)
        got = await store.get_tokens()
        assert got is not None
        assert got.access_token == "at"
        assert got.refresh_token == "rt"
        assert got.scope == "read:docs"

    async def test_get_unknown_returns_none(self, storage_backend, token_key):
        store = DBTokenStorage(
            backend=storage_backend,
            user_id="U1",
            server_id="srv",
            redirect_uri="https://agent.example.com/oauth/callback",
            token_key=token_key,
        )
        assert await store.get_tokens() is None

    async def test_refresh_token_encrypted_at_rest(self, storage_backend, token_key):
        store = DBTokenStorage(
            backend=storage_backend,
            user_id="U1",
            server_id="srv",
            redirect_uri="https://agent.example.com/oauth/callback",
            token_key=token_key,
        )
        token = OAuthToken(
            access_token="at",
            token_type="Bearer",
            expires_in=3600,
            refresh_token="VERY_SECRET",
            scope="",
        )
        await store.set_tokens(token)
        # Poke into the underlying row directly.
        row = await storage_backend.get_oauth_token("U1", "srv")
        assert row is not None
        assert row.refresh_token_enc is not None
        assert "VERY_SECRET" not in row.refresh_token_enc

    async def test_decrypt_failure_deletes_row(self, storage_backend, token_key):
        store_a = DBTokenStorage(
            backend=storage_backend,
            user_id="U1",
            server_id="srv",
            redirect_uri="https://agent.example.com/oauth/callback",
            token_key=token_key,
        )
        await store_a.set_tokens(
            OAuthToken(
                access_token="at", token_type="Bearer", expires_in=60, refresh_token="rt", scope=""
            )
        )
        # Reopen with a different key — decryption fails.
        _, other_key = derive_subkeys(secrets.token_bytes(32))
        store_b = DBTokenStorage(
            backend=storage_backend,
            user_id="U1",
            server_id="srv",
            redirect_uri="https://agent.example.com/oauth/callback",
            token_key=other_key,
        )
        assert await store_b.get_tokens() is None
        # Row should have been deleted.
        assert await storage_backend.get_oauth_token("U1", "srv") is None


class TestClientInfo:
    async def test_roundtrip(self, storage_backend, token_key):
        store = DBTokenStorage(
            backend=storage_backend,
            user_id="U1",
            server_id="srv",
            redirect_uri="https://agent.example.com/oauth/callback",
            token_key=token_key,
        )
        info = OAuthClientInformationFull(
            client_id="cid",
            client_secret=None,
            redirect_uris=["https://agent.example.com/oauth/callback"],
        )
        await store.set_client_info(info)
        got = await store.get_client_info()
        assert got is not None
        assert got.client_id == "cid"

    async def test_client_info_shared_across_users_for_same_server(
        self, storage_backend, token_key
    ):
        store_a = DBTokenStorage(
            backend=storage_backend,
            user_id="UA",
            server_id="srv",
            redirect_uri="https://agent.example.com/oauth/callback",
            token_key=token_key,
        )
        store_b = DBTokenStorage(
            backend=storage_backend,
            user_id="UB",
            server_id="srv",
            redirect_uri="https://agent.example.com/oauth/callback",
            token_key=token_key,
        )
        await store_a.set_client_info(
            OAuthClientInformationFull(
                client_id="cid",
                redirect_uris=["https://agent.example.com/oauth/callback"],
            )
        )
        got = await store_b.get_client_info()
        assert got is not None
        assert got.client_id == "cid"

    async def test_client_info_isolated_by_redirect_uri(self, storage_backend, token_key):
        """Same server but different redirect_uri must register independently."""
        store_a = DBTokenStorage(
            backend=storage_backend,
            user_id="U1",
            server_id="srv",
            redirect_uri="https://agent-a.example.com/oauth/callback",
            token_key=token_key,
        )
        store_b = DBTokenStorage(
            backend=storage_backend,
            user_id="U1",
            server_id="srv",
            redirect_uri="https://agent-b.example.com/oauth/callback",
            token_key=token_key,
        )
        await store_a.set_client_info(
            OAuthClientInformationFull(
                client_id="cid-a",
                redirect_uris=["https://agent-a.example.com/oauth/callback"],
            )
        )
        # store_b sees nothing — different redirect_uri, different cache key.
        assert await store_b.get_client_info() is None
        await store_b.set_client_info(
            OAuthClientInformationFull(
                client_id="cid-b",
                redirect_uris=["https://agent-b.example.com/oauth/callback"],
            )
        )
        # Both rows coexist.
        got_a = await store_a.get_client_info()
        got_b = await store_b.get_client_info()
        assert got_a is not None and got_a.client_id == "cid-a"
        assert got_b is not None and got_b.client_id == "cid-b"
