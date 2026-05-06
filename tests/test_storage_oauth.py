"""Tests for OAuth-related methods on the SQLite storage backend.

Postgres has the same surface; integration tests exercise it elsewhere.
"""

import time

import pytest

from slack_agents.storage.base import OAuthClientRow, OAuthTokenRow
from slack_agents.storage.sqlite import Provider as SqliteProvider


@pytest.fixture
async def storage():
    s = SqliteProvider(path=":memory:")
    await s.initialize()
    yield s
    await s.close()


class TestOAuthTokens:
    async def test_get_unknown_returns_none(self, storage):
        assert await storage.get_oauth_token("U1", "srv") is None

    async def test_put_then_get(self, storage):
        now = int(time.time())
        row = OAuthTokenRow(
            access_token="at",
            refresh_token_enc="enc-rt",
            token_type="Bearer",
            scopes="read:docs",
            expires_at=now + 3600,
            created_at=now,
            updated_at=now,
        )
        await storage.put_oauth_token("U1", "srv", row)
        got = await storage.get_oauth_token("U1", "srv")
        assert got == row

    async def test_put_updates_existing(self, storage):
        now = int(time.time())
        row1 = OAuthTokenRow("a1", None, "Bearer", "", None, now, now)
        row2 = OAuthTokenRow("a2", "enc", "Bearer", "x", now + 60, now, now + 1)
        await storage.put_oauth_token("U1", "srv", row1)
        await storage.put_oauth_token("U1", "srv", row2)
        got = await storage.get_oauth_token("U1", "srv")
        assert got.access_token == "a2"
        assert got.refresh_token_enc == "enc"

    async def test_delete(self, storage):
        now = int(time.time())
        row = OAuthTokenRow("a", None, "Bearer", "", None, now, now)
        await storage.put_oauth_token("U1", "srv", row)
        await storage.delete_oauth_token("U1", "srv")
        assert await storage.get_oauth_token("U1", "srv") is None

    async def test_isolation_per_user_and_server(self, storage):
        now = int(time.time())
        a = OAuthTokenRow("at-A", None, "Bearer", "", None, now, now)
        b = OAuthTokenRow("at-B", None, "Bearer", "", None, now, now)
        await storage.put_oauth_token("U1", "srv", a)
        await storage.put_oauth_token("U2", "srv", b)
        assert (await storage.get_oauth_token("U1", "srv")).access_token == "at-A"
        assert (await storage.get_oauth_token("U2", "srv")).access_token == "at-B"


class TestOAuthClients:
    async def test_get_unknown_returns_none(self, storage):
        assert await storage.get_oauth_client("srv") is None

    async def test_put_then_get(self, storage):
        now = int(time.time())
        row = OAuthClientRow(
            client_id="cid",
            client_secret=None,
            metadata_json='{"client_id":"cid"}',
            authorization_server="https://idp.example.com",
            created_at=now,
            updated_at=now,
        )
        await storage.put_oauth_client("srv", row)
        got = await storage.get_oauth_client("srv")
        assert got == row

    async def test_put_updates_existing(self, storage):
        now = int(time.time())
        await storage.put_oauth_client(
            "srv",
            OAuthClientRow("c1", None, "{}", "https://a", now, now),
        )
        await storage.put_oauth_client(
            "srv",
            OAuthClientRow("c2", "secret", "{}", "https://b", now, now + 1),
        )
        got = await storage.get_oauth_client("srv")
        assert got.client_id == "c2"
        assert got.client_secret == "secret"
