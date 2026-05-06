"""Tests for `_classify_llm_error` in slack/agent.py.

This is the helper that turns raw LLM-provider exceptions (Anthropic API
errors, etc.) into a (user_message, log_level) pair so transient overload /
rate-limit errors get a friendly user message and a single warning line in
logs, while unexpected / configuration errors still get full ERROR tracebacks.
"""

import logging

from slack_agents.slack.agent import _classify_llm_error


class _FakeAnthropicError(Exception):
    """Mimics anthropic.APIStatusError — has a `body` dict with `error.type`."""

    def __init__(self, error_type: str, message: str = "msg"):
        super().__init__(message)
        self.body = {
            "type": "error",
            "error": {"type": error_type, "message": message},
            "request_id": "req_test",
        }


class TestKnownTransientErrors:
    def test_overloaded_error_warning(self):
        msg, level = _classify_llm_error(_FakeAnthropicError("overloaded_error"))
        assert "overloaded" in msg.lower()
        assert "try" in msg.lower()
        assert level == logging.WARNING

    def test_rate_limit_warning(self):
        msg, level = _classify_llm_error(_FakeAnthropicError("rate_limit_error"))
        assert "rate limit" in msg.lower()
        assert level == logging.WARNING

    def test_api_error_warning(self):
        msg, level = _classify_llm_error(_FakeAnthropicError("api_error"))
        assert "internal error" in msg.lower()
        assert level == logging.WARNING


class TestKnownConfigurationErrors:
    def test_authentication_error_full_error(self):
        msg, level = _classify_llm_error(_FakeAnthropicError("authentication_error"))
        assert "credentials" in msg.lower() or "configuration" in msg.lower()
        assert level == logging.ERROR

    def test_invalid_request_error(self):
        msg, level = _classify_llm_error(_FakeAnthropicError("invalid_request_error"))
        assert "bug" in msg.lower() or "invalid" in msg.lower()
        assert level == logging.ERROR


class TestUnknownErrors:
    def test_plain_exception_falls_back_generic(self):
        msg, level = _classify_llm_error(RuntimeError("boom"))
        assert msg == "Sorry, I encountered an error processing your request."
        assert level == logging.ERROR

    def test_unknown_error_type_falls_back_generic(self):
        msg, level = _classify_llm_error(_FakeAnthropicError("brand_new_error_type"))
        assert msg == "Sorry, I encountered an error processing your request."
        assert level == logging.ERROR

    def test_body_not_dict_falls_back_generic(self):
        e = Exception("no body")
        e.body = "not a dict"  # type: ignore[attr-defined]
        msg, level = _classify_llm_error(e)
        assert msg == "Sorry, I encountered an error processing your request."
        assert level == logging.ERROR
