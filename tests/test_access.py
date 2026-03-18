"""Tests for access-control providers and integration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from slack_agents import UserConversationContext
from slack_agents.access.allow_all import Provider as AllowAllProvider
from slack_agents.access.allow_list import Provider as AllowListProvider
from slack_agents.access.base import AccessDenied

DENY_MSG = "Not allowed. Ask in #help-infra."


def _ctx(user_id="U123", **overrides):
    """Build a full UserConversationContext with sensible defaults."""
    defaults = dict(
        user_id=user_id,
        user_name="test-user",
        user_handle="test-user",
        channel_id="C001",
        channel_name="general",
        thread_id="1234.5678",
    )
    defaults.update(overrides)
    return UserConversationContext(**defaults)


class TestAccessDenied:
    def test_str(self):
        exc = AccessDenied("nope")
        assert str(exc) == "nope"


class TestUserConversationContext:
    def test_all_fields(self):
        ctx = UserConversationContext(
            user_id="U123",
            user_name="alice",
            user_handle="alice",
            channel_id="C456",
            channel_name="general",
            thread_id="1234.5678",
        )
        assert ctx["user_id"] == "U123"
        assert ctx["user_name"] == "alice"
        assert ctx["user_handle"] == "alice"
        assert ctx["channel_id"] == "C456"
        assert ctx["channel_name"] == "general"
        assert ctx["thread_id"] == "1234.5678"


class TestAllowAllProvider:
    @pytest.mark.asyncio
    async def test_allows_any_user(self):
        provider = AllowAllProvider()
        r = await provider.check_access(context=_ctx())
        assert isinstance(r, dict)

    @pytest.mark.asyncio
    async def test_does_not_raise(self):
        provider = AllowAllProvider()
        await provider.check_access(context=_ctx("UXXX"))  # no exception


class TestAllowListProvider:
    @pytest.mark.asyncio
    async def test_allows_listed_user(self):
        provider = AllowListProvider(userid_list=["U111", "U222"], deny_message=DENY_MSG)
        r = await provider.check_access(context=_ctx("U111"))
        assert isinstance(r, dict)

    @pytest.mark.asyncio
    async def test_denies_unlisted_user(self):
        provider = AllowListProvider(userid_list=["U111"], deny_message=DENY_MSG)
        with pytest.raises(AccessDenied, match=DENY_MSG):
            await provider.check_access(context=_ctx("U999"))

    @pytest.mark.asyncio
    async def test_empty_list_denies_all(self):
        provider = AllowListProvider(userid_list=[], deny_message=DENY_MSG)
        with pytest.raises(AccessDenied):
            await provider.check_access(context=_ctx("U111"))


class TestAgentAccessIntegration:
    """Integration: verify _handle_message short-circuits when access is denied."""

    @pytest.mark.asyncio
    async def test_denied_user_gets_ephemeral_no_llm_call(self):
        from slack_agents.config import AgentConfig
        from slack_agents.slack.agent import SlackAgent

        config = AgentConfig(
            version="1.0.0",
            slack={"bot_token": "xoxb-test", "app_token": "xapp-test"},
            llm={"type": "slack_agents.llm.anthropic", "model": "test", "api_key": "k"},
            storage={"type": "slack_agents.storage.sqlite"},
            access={
                "type": "slack_agents.access.allow_list",
                "userid_list": ["U_ALLOWED"],
                "deny_message": DENY_MSG,
            },
        )

        with patch("slack_agents.slack.agent.load_plugin") as mock_load:
            mock_llm = MagicMock()
            mock_access = AllowListProvider(userid_list=["U_ALLOWED"], deny_message=DENY_MSG)
            mock_load.side_effect = [mock_llm, mock_access]

            agent = SlackAgent(config, system_prompt="test", agent_name="test-agent")

        client = AsyncMock()
        say = AsyncMock()

        await agent._handle_message(
            text="hello",
            channel="C123",
            thread_ts="1234.5678",
            files=[],
            say=say,
            client=client,
            user_id="U_DENIED",
        )

        client.chat_postEphemeral.assert_called_once_with(
            channel="C123",
            thread_ts="1234.5678",
            user="U_DENIED",
            text=DENY_MSG,
        )
        client.assistant_threads_setStatus.assert_not_called()
        say.assert_not_called()

    @pytest.mark.asyncio
    async def test_allowed_user_proceeds(self):
        from slack_agents.config import AgentConfig
        from slack_agents.slack.agent import SlackAgent

        config = AgentConfig(
            version="1.0.0",
            slack={"bot_token": "xoxb-test", "app_token": "xapp-test"},
            llm={"type": "slack_agents.llm.anthropic", "model": "test", "api_key": "k"},
            storage={"type": "slack_agents.storage.sqlite"},
            access={
                "type": "slack_agents.access.allow_list",
                "userid_list": ["U_ALLOWED"],
                "deny_message": DENY_MSG,
            },
        )

        with patch("slack_agents.slack.agent.load_plugin") as mock_load:
            mock_llm = MagicMock()
            mock_access = AllowListProvider(userid_list=["U_ALLOWED"], deny_message=DENY_MSG)
            mock_load.side_effect = [mock_llm, mock_access]

            agent = SlackAgent(config, system_prompt="test", agent_name="test-agent")

        client = AsyncMock()
        say = AsyncMock()

        await agent._handle_message(
            text="hello",
            channel="C123",
            thread_ts="1234.5678",
            files=[],
            say=say,
            client=client,
            user_id="U_ALLOWED",
        )

        client.chat_postEphemeral.assert_not_called()
        client.assistant_threads_setStatus.assert_called()
