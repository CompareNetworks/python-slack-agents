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
    REDIRECT = "https://agent.example.com/oauth/callback"

    async def test_get_unknown_returns_none(self, storage):
        assert await storage.get_oauth_client("srv", self.REDIRECT) is None

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
        await storage.put_oauth_client("srv", self.REDIRECT, row)
        got = await storage.get_oauth_client("srv", self.REDIRECT)
        assert got == row

    async def test_put_updates_existing(self, storage):
        now = int(time.time())
        await storage.put_oauth_client(
            "srv",
            self.REDIRECT,
            OAuthClientRow("c1", None, "{}", "https://a", now, now),
        )
        await storage.put_oauth_client(
            "srv",
            self.REDIRECT,
            OAuthClientRow("c2", "secret", "{}", "https://b", now, now + 1),
        )
        got = await storage.get_oauth_client("srv", self.REDIRECT)
        assert got.client_id == "c2"
        assert got.client_secret == "secret"

    async def test_delete(self, storage):
        now = int(time.time())
        await storage.put_oauth_client(
            "srv",
            self.REDIRECT,
            OAuthClientRow("c", None, "{}", "https://a", now, now),
        )
        await storage.delete_oauth_client("srv", self.REDIRECT)
        assert await storage.get_oauth_client("srv", self.REDIRECT) is None

    async def test_isolation_per_redirect_uri(self, storage):
        """Same server, different redirect_uri → independent cache rows."""
        now = int(time.time())
        red_a = "https://agent-a.example.com/oauth/callback"
        red_b = "https://agent-b.example.com/oauth/callback"
        await storage.put_oauth_client(
            "srv", red_a, OAuthClientRow("c-a", None, "{}", "https://idp", now, now)
        )
        await storage.put_oauth_client(
            "srv", red_b, OAuthClientRow("c-b", None, "{}", "https://idp", now, now)
        )
        got_a = await storage.get_oauth_client("srv", red_a)
        got_b = await storage.get_oauth_client("srv", red_b)
        assert got_a is not None and got_a.client_id == "c-a"
        assert got_b is not None and got_b.client_id == "c-b"
        # Deleting one does not affect the other.
        await storage.delete_oauth_client("srv", red_a)
        assert await storage.get_oauth_client("srv", red_a) is None
        assert (await storage.get_oauth_client("srv", red_b)).client_id == "c-b"
