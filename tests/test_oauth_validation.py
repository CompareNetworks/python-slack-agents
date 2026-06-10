"""Tests for validate_ingress_env (OAuth + A2A-push HTTP ingress)."""

import base64

import pytest

from slack_agents.config import ingress_needed, oauth_needed, validate_ingress_env

OAUTH = {"x": {"type": "slack_agents.tools.mcp_http_oauth"}}
PUSH = {"p": {"type": "slack_agents.a2a.agent", "push_notifications": True}}
A2A_OAUTH = {"my-agent": {"type": "slack_agents.a2a.agent", "auth": {"type": "oauth2"}}}


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


class TestA2AOAuth:
    def test_a2a_oauth_triggers_ingress_needed(self):
        assert ingress_needed(A2A_OAUTH) is True

    def test_a2a_oauth_triggers_oauth_needed(self):
        assert oauth_needed(A2A_OAUTH) is True

    def test_a2a_oauth_missing_vars_raises_with_agent_name(self):
        with pytest.raises(SystemExit) as excinfo:
            validate_ingress_env(A2A_OAUTH)
        msg = str(excinfo.value)
        assert "PUBLIC_URL" in msg
        assert "OAUTH_SECRET_KEY" in msg
        assert "my-agent" in msg
        assert "OAuth-protected A2A agents" in msg

    def test_a2a_oauth_missing_public_url_raises(self, monkeypatch):
        monkeypatch.setenv("OAUTH_SECRET_KEY", _good_key())
        with pytest.raises(SystemExit) as excinfo:
            validate_ingress_env(A2A_OAUTH)
        msg = str(excinfo.value)
        assert "PUBLIC_URL" in msg
        assert "my-agent" in msg

    def test_a2a_oauth_missing_secret_key_raises(self, monkeypatch):
        monkeypatch.setenv("PUBLIC_URL", "https://a.example.com")
        with pytest.raises(SystemExit) as excinfo:
            validate_ingress_env(A2A_OAUTH)
        msg = str(excinfo.value)
        assert "OAUTH_SECRET_KEY" in msg
        assert "my-agent" in msg

    def test_a2a_oauth_valid_env_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("PUBLIC_URL", "https://a.example.com")
        monkeypatch.setenv("OAUTH_SECRET_KEY", _good_key())
        validate_ingress_env(A2A_OAUTH)  # must not raise

    def test_a2a_static_bearer_does_not_trigger_oauth(self):
        static = {
            "agent": {"type": "slack_agents.a2a.agent", "auth": {"type": "bearer", "token": "x"}}
        }
        assert oauth_needed(static) is False
        assert ingress_needed(static) is False

    def test_a2a_no_auth_does_not_trigger_oauth(self):
        no_auth = {"agent": {"type": "slack_agents.a2a.agent"}}
        assert oauth_needed(no_auth) is False
        assert ingress_needed(no_auth) is False
