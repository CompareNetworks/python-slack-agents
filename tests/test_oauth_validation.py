"""Tests for validate_ingress_env (OAuth + A2A-push HTTP ingress)."""

import base64

import pytest

from slack_agents.config import validate_ingress_env

OAUTH = {"x": {"type": "slack_agents.tools.mcp_http_oauth"}}
PUSH = {"p": {"type": "slack_agents.a2a.agent", "push_notifications": True}}


def _good_key() -> str:
    return base64.b64encode(b"\x00" * 32).decode()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    monkeypatch.delenv("OAUTH_SECRET_KEY", raising=False)


class TestNothingConfigured:
    def test_returns_silently(self):
        validate_ingress_env({"foo": {"type": "slack_agents.tools.mcp_http"}})


class TestRequiredVarsMissing:
    def test_oauth_missing_public_url_raises(self, monkeypatch):
        monkeypatch.setenv("OAUTH_SECRET_KEY", _good_key())
        with pytest.raises(SystemExit) as excinfo:
            validate_ingress_env(OAUTH)
        assert "PUBLIC_URL" in str(excinfo.value)

    def test_push_missing_public_url_raises(self):
        with pytest.raises(SystemExit) as excinfo:
            validate_ingress_env(PUSH)
        msg = str(excinfo.value)
        assert "PUBLIC_URL" in msg
        assert "push-enabled A2A agents" in msg

    def test_missing_secret_key_raises_with_openssl_hint(self, monkeypatch):
        monkeypatch.setenv("PUBLIC_URL", "https://a.example.com")
        with pytest.raises(SystemExit) as excinfo:
            validate_ingress_env(OAUTH)
        msg = str(excinfo.value)
        assert "OAUTH_SECRET_KEY" in msg
        assert "openssl rand -base64 32" in msg

    def test_short_secret_key_rejected(self, monkeypatch):
        monkeypatch.setenv("PUBLIC_URL", "https://a.example.com")
        monkeypatch.setenv("OAUTH_SECRET_KEY", base64.b64encode(b"short").decode())
        with pytest.raises(SystemExit) as excinfo:
            validate_ingress_env(OAUTH)
        assert "OAUTH_SECRET_KEY" in str(excinfo.value)

    def test_push_only_needs_no_secret_key(self, monkeypatch):
        monkeypatch.setenv("PUBLIC_URL", "http://127.0.0.1:8080")
        validate_ingress_env(PUSH)  # no OAUTH_SECRET_KEY required for push


class TestPublicUrlScheme:
    @pytest.mark.parametrize(
        "url",
        [
            "https://agent.example.com",
            "http://localhost:8080",
            "http://127.0.0.1:8080",
            "http://[::1]:8080",
        ],
    )
    def test_accepted(self, monkeypatch, url):
        monkeypatch.setenv("PUBLIC_URL", url)
        monkeypatch.setenv("OAUTH_SECRET_KEY", _good_key())
        validate_ingress_env(OAUTH)

    @pytest.mark.parametrize("url", ["http://example.com", "ftp://example.com", "not-a-url"])
    def test_rejected(self, monkeypatch, url):
        monkeypatch.setenv("PUBLIC_URL", url)
        monkeypatch.setenv("OAUTH_SECRET_KEY", _good_key())
        with pytest.raises(SystemExit):
            validate_ingress_env(OAUTH)


class TestMultipleErrors:
    def test_consolidates(self):
        with pytest.raises(SystemExit) as excinfo:
            validate_ingress_env(OAUTH)
        msg = str(excinfo.value)
        assert "PUBLIC_URL" in msg
        assert "OAUTH_SECRET_KEY" in msg
