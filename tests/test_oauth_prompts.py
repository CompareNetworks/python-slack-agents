"""Tests for oauth.prompts — Slack ephemeral auth-prompt builder."""

from unittest.mock import AsyncMock

import pytest
from slack_sdk.errors import SlackApiError

from slack_agents.oauth.prompts import AuthPromptDeliveryError, send_auth_prompt


class TestSendAuthPrompt:
    async def test_calls_post_ephemeral_with_button_url(self):
        client = AsyncMock()
        client.chat_postEphemeral = AsyncMock(return_value={"ok": True})
        await send_auth_prompt(
            slack_client=client,
            user_id="U1",
            channel_id="C1",
            thread_id="1234.5678",
            server_name="my-mcp",
            signed_state="SIGNED",
            public_url="https://agent.example.com",
        )
        client.chat_postEphemeral.assert_awaited_once()
        kwargs = client.chat_postEphemeral.await_args.kwargs
        assert kwargs["channel"] == "C1"
        assert kwargs["user"] == "U1"
        assert kwargs["thread_ts"] == "1234.5678"
        # Find the button URL in the blocks payload.
        blocks = kwargs["blocks"]
        urls = [
            el.get("url")
            for blk in blocks
            for el in blk.get("elements", [])
            if isinstance(el, dict) and "url" in el
        ]
        assert "https://agent.example.com/oauth/start/SIGNED" in urls

    async def test_no_thread_id_omits_thread_ts(self):
        client = AsyncMock()
        client.chat_postEphemeral = AsyncMock(return_value={"ok": True})
        await send_auth_prompt(
            slack_client=client,
            user_id="U1",
            channel_id="C1",
            thread_id=None,
            server_name="my-mcp",
            signed_state="X",
            public_url="https://agent.example.com",
        )
        kwargs = client.chat_postEphemeral.await_args.kwargs
        assert "thread_ts" not in kwargs

    async def test_slack_error_raises_typed_error(self):
        client = AsyncMock()
        client.chat_postEphemeral = AsyncMock(
            side_effect=SlackApiError("not_in_channel", response={"error": "not_in_channel"})
        )
        with pytest.raises(AuthPromptDeliveryError):
            await send_auth_prompt(
                slack_client=client,
                user_id="U1",
                channel_id="C1",
                thread_id=None,
                server_name="my-mcp",
                signed_state="X",
                public_url="https://agent.example.com",
            )
