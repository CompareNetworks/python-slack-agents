"""Build and deliver the Slack ephemeral auth prompt."""

from __future__ import annotations

import logging
from typing import Any

from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)


class AuthPromptDeliveryError(Exception):
    """Raised when chat.postEphemeral fails — Provider should surface to the LLM."""


async def send_auth_prompt(
    *,
    slack_client: Any,
    user_id: str,
    channel_id: str,
    thread_id: str | None,
    server_name: str,
    signed_state: str,
    public_url: str,
) -> None:
    """Post a chat.postEphemeral with an Authenticate button visible only to the user."""
    auth_url = f"{public_url}/oauth/start/{signed_state}"
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"🔐 *{server_name}* needs access to act on your behalf.\n"
                    f"Click below to authenticate. The link expires in 5 minutes."
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Authenticate"},
                    "url": auth_url,
                    "style": "primary",
                    # Stable action_id so the agent's no-op handler can ack the
                    # interactivity event Slack sends when a URL button is clicked.
                    "action_id": "oauth_authenticate",
                }
            ],
        },
    ]
    kwargs: dict[str, Any] = {
        "channel": channel_id,
        "user": user_id,
        "blocks": blocks,
        "text": f"{server_name} needs authentication — click to continue.",
    }
    if thread_id:
        kwargs["thread_ts"] = thread_id
    try:
        await slack_client.chat_postEphemeral(**kwargs)
    except SlackApiError as e:
        logger.warning("oauth: chat.postEphemeral failed: %s", e.response.get("error"))
        raise AuthPromptDeliveryError(str(e)) from e
