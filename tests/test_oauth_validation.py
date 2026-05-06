"""Tests for validate_oauth_env."""

import base64

import pytest

from slack_agents.config import validate_oauth_env


def _good_key() -> str:
    return base64.b64encode(b"\x00" * 32).decode()


class TestNoOAuthProvidersConfigured:
    def test_returns_silently_with_no_env(self, monkeypatch):
        monkeypatch.delenv("OAUTH_PUBLIC_URL", raising=False)
        monkeypatch.delenv("OAUTH_SECRET_KEY", raising=False)
        validate_oauth_env({"foo": {"type": "slack_agents.tools.mcp_http"}})


class TestRequiredVarsMissing:
    def test_missing_public_url_raises(self, monkeypatch):
        monkeypatch.delenv("OAUTH_PUBLIC_URL", raising=False)
        monkeypatch.setenv("OAUTH_SECRET_KEY", _good_key())
        with pytest.raises(SystemExit) as excinfo:
            validate_oauth_env({"x": {"type": "slack_agents.tools.mcp_http_oauth"}})
        assert "OAUTH_PUBLIC_URL" in str(excinfo.value)

    def test_missing_secret_key_raises_with_openssl_hint(self, monkeypatch):
        monkeypatch.setenv("OAUTH_PUBLIC_URL", "https://a.example.com")
        monkeypatch.delenv("OAUTH_SECRET_KEY", raising=False)
        with pytest.raises(SystemExit) as excinfo:
            validate_oauth_env({"x": {"type": "slack_agents.tools.mcp_http_oauth"}})
        msg = str(excinfo.value)
        assert "OAUTH_SECRET_KEY" in msg
        assert "openssl rand -base64 32" in msg

    def test_short_secret_key_rejected(self, monkeypatch):
        monkeypatch.setenv("OAUTH_PUBLIC_URL", "https://a.example.com")
        monkeypatch.setenv("OAUTH_SECRET_KEY", base64.b64encode(b"short").decode())
        with pytest.raises(SystemExit) as excinfo:
            validate_oauth_env({"x": {"type": "slack_agents.tools.mcp_http_oauth"}})
        assert "OAUTH_SECRET_KEY" in str(excinfo.value)


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
        monkeypatch.setenv("OAUTH_PUBLIC_URL", url)
        monkeypatch.setenv("OAUTH_SECRET_KEY", _good_key())
        validate_oauth_env({"x": {"type": "slack_agents.tools.mcp_http_oauth"}})

    @pytest.mark.parametrize("url", ["http://example.com", "ftp://example.com", "not-a-url"])
    def test_rejected(self, monkeypatch, url):
        monkeypatch.setenv("OAUTH_PUBLIC_URL", url)
        monkeypatch.setenv("OAUTH_SECRET_KEY", _good_key())
        with pytest.raises(SystemExit):
            validate_oauth_env({"x": {"type": "slack_agents.tools.mcp_http_oauth"}})


class TestMultipleErrors:
    def test_consolidates(self, monkeypatch):
        monkeypatch.delenv("OAUTH_PUBLIC_URL", raising=False)
        monkeypatch.delenv("OAUTH_SECRET_KEY", raising=False)
        with pytest.raises(SystemExit) as excinfo:
            validate_oauth_env({"x": {"type": "slack_agents.tools.mcp_http_oauth"}})
        msg = str(excinfo.value)
        assert "OAUTH_PUBLIC_URL" in msg
        assert "OAUTH_SECRET_KEY" in msg
