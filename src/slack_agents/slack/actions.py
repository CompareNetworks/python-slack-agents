"""Slack interactive components: buttons, actions, and confirmation dialogs."""

import json
import logging
import re
import uuid

from slack_agents.slack.tool_blocks import (
    ICON_SUCCESS,
    build_collapsed_blocks,
    build_expanded_blocks,
)

logger = logging.getLogger(__name__)


def build_confirmation_blocks(
    prompt_text: str,
    action_id_prefix: str | None = None,
) -> list[dict]:
    prefix = action_id_prefix or uuid.uuid4().hex[:8]
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": prompt_text},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": f"agent_approve_{prefix}",
                    "value": json.dumps({"action": "approve", "prefix": prefix}),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Deny"},
                    "style": "danger",
                    "action_id": f"agent_deny_{prefix}",
                    "value": json.dumps({"action": "deny", "prefix": prefix}),
                },
            ],
        },
    ]


def register_action_handlers(app, callback) -> None:
    @app.action(re.compile(r"^agent_(approve|deny)_"))
    async def handle_agent_action(ack, action, body, respond):
        await ack()
        try:
            value = json.loads(action.get("value", "{}"))
            await callback(value, body, respond)
        except Exception:
            logger.exception("Error handling agent action")
            await respond(text="Error processing your response.")


def register_tool_toggle_handlers(app, get_conversation_manager) -> None:
    """Register expand/collapse handlers for tool call detail buttons.

    Args:
        app: The Bolt AsyncApp instance.
        get_conversation_manager: Callable returning the ConversationManager.
    """

    @app.action(re.compile(r"^tool_(expand|collapse)_"))
    async def handle_tool_toggle(ack, action, body, client):
        await ack()
        try:
            raw = action.get("selected_option", {}).get("value", "{}")
            value = json.loads(raw)
            tool_id = value["tool_id"]
            tool_name = value["tool_name"]
            action_id = action["action_id"]
            expanding = action_id.startswith("tool_expand_")

            channel = body["channel"]["id"]
            message_ts = body["message"]["ts"]

            mgr = get_conversation_manager()
            data = await mgr.get_tool_call(tool_id)
            if data is None:
                expired_msg = (
                    f"{ICON_SUCCESS} tool: _{tool_name}_  \u2014  Details no longer available"
                )
                blocks = [
                    {
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": expired_msg}],
                    }
                ]
                await client.chat_update(
                    channel=channel,
                    ts=message_ts,
                    text=f"Tool {tool_name} (details expired).",
                    blocks=blocks,
                )
                return

            is_error = data["is_error"]
            if expanding:
                blocks = build_expanded_blocks(
                    tool_name, is_error, tool_id, data["input_json"], data["output_json"]
                )
            else:
                blocks = build_collapsed_blocks(tool_name, is_error, tool_id)

            fallback = f"Tool {tool_name} {'failed' if is_error else 'complete'}."
            await client.chat_update(
                channel=channel,
                ts=message_ts,
                text=fallback,
                blocks=blocks,
            )
        except Exception:
            logger.exception("Error handling tool toggle action")
