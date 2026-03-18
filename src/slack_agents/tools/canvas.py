"""Built-in tool: Slack canvas management — file-like API.

Exports a Provider class that subclasses BaseToolProvider.
"""

import json
import logging

from slack_sdk.web.async_client import AsyncWebClient

from slack_agents import UserConversationContext
from slack_agents.slack.canvases import (
    CanvasError,
    create_canvas,
    delete_canvas,
    delete_canvas_access,
    edit_canvas,
    get_canvas_info,
    get_canvas_permalink,
    list_canvases,
    read_canvas_content,
    set_canvas_access,
)
from slack_agents.storage.base import BaseStorageProvider
from slack_agents.tools.base import BaseToolProvider, ToolResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _canvas_create(
    client: AsyncWebClient, arguments: dict, user_conversation_context: UserConversationContext
) -> ToolResult:
    title = arguments.get("title")
    content = arguments.get("content")
    channel_id = arguments.get("channel_id") or user_conversation_context["channel_id"]
    resp = await create_canvas(client, title=title, markdown=content, channel_id=channel_id)
    canvas_id = resp.get("canvas_id", "unknown")
    result: dict = {"id": canvas_id}
    if title:
        result["title"] = title
    permalink = await get_canvas_permalink(client, canvas_id=canvas_id)
    if permalink:
        result["permalink"] = permalink
    return {"content": json.dumps(result), "is_error": False, "files": []}


async def _canvas_get(
    client: AsyncWebClient, arguments: dict, user_conversation_context: UserConversationContext
) -> ToolResult:
    canvas_id = arguments["id"]
    data = await read_canvas_content(client, canvas_id=canvas_id)
    result: dict = {
        "id": canvas_id,
        "title": data.get("title", ""),
        "content": data.get("content", ""),
    }
    permalink = await get_canvas_permalink(client, canvas_id=canvas_id)
    if permalink:
        result["permalink"] = permalink
    return {"content": json.dumps(result), "is_error": False, "files": []}


async def _canvas_update(
    client: AsyncWebClient, arguments: dict, user_conversation_context: UserConversationContext
) -> ToolResult:
    canvas_id = arguments["id"]
    content = arguments.get("content")
    title = arguments.get("title")

    changes: list[dict] = []
    if content is not None:
        changes.append(
            {
                "operation": "replace",
                "document_content": {"type": "markdown", "markdown": content},
            }
        )
    if title is not None:
        changes.append(
            {
                "operation": "rename",
                "title_content": {"type": "markdown", "markdown": title},
            }
        )

    if changes:
        await edit_canvas(client, canvas_id=canvas_id, changes=changes)

    result: dict = {"id": canvas_id}
    if title is not None:
        result["title"] = title
    permalink = await get_canvas_permalink(client, canvas_id=canvas_id)
    if permalink:
        result["permalink"] = permalink
    return {"content": json.dumps(result), "is_error": False, "files": []}


async def _canvas_delete(
    client: AsyncWebClient, arguments: dict, user_conversation_context: UserConversationContext
) -> ToolResult:
    canvas_id = arguments["id"]
    await delete_canvas(client, canvas_id=canvas_id)
    result = {"id": canvas_id, "deleted": True}
    return {"content": json.dumps(result), "is_error": False, "files": []}


async def _canvas_list(
    client: AsyncWebClient, arguments: dict, user_conversation_context: UserConversationContext
) -> ToolResult:
    channel_id = arguments.get("channel_id")
    files = await list_canvases(client, channel=channel_id)
    items = []
    for f in files:
        items.append(
            {
                "id": f.get("id"),
                "title": f.get("title", "(untitled)"),
                "created": f.get("created"),
                "updated": f.get("updated"),
            }
        )
    return {"content": json.dumps({"canvases": items}), "is_error": False, "files": []}


async def _canvas_access_get(
    client: AsyncWebClient, arguments: dict, user_conversation_context: UserConversationContext
) -> ToolResult:
    canvas_id = arguments["id"]
    file_info = await get_canvas_info(client, canvas_id=canvas_id)
    shares = file_info.get("shares", {})
    access: list[dict] = []
    # shares is typically {"public": {"C123": [...]}, "private": {"C456": [...]}}
    for share_type, channels in shares.items():
        if isinstance(channels, dict):
            for entity_id in channels:
                access.append(
                    {
                        "entity_id": entity_id,
                        "entity_type": "channel",
                        "access_level": share_type,
                    }
                )
    result: dict = {"id": canvas_id, "access": access}
    return {"content": json.dumps(result), "is_error": False, "files": []}


async def _canvas_access_add(
    client: AsyncWebClient, arguments: dict, user_conversation_context: UserConversationContext
) -> ToolResult:
    canvas_id = arguments["id"]
    access_level = arguments["access_level"]
    user_ids = arguments.get("user_ids")
    channel_ids = arguments.get("channel_ids")
    await set_canvas_access(
        client,
        canvas_id=canvas_id,
        access_level=access_level,
        user_ids=user_ids,
        channel_ids=channel_ids,
    )
    result: dict = {"id": canvas_id, "access_level": access_level}
    if user_ids:
        result["user_ids"] = user_ids
    if channel_ids:
        result["channel_ids"] = channel_ids
    return {"content": json.dumps(result), "is_error": False, "files": []}


async def _canvas_access_remove(
    client: AsyncWebClient, arguments: dict, user_conversation_context: UserConversationContext
) -> ToolResult:
    canvas_id = arguments["id"]
    user_ids = arguments.get("user_ids")
    channel_ids = arguments.get("channel_ids")
    await delete_canvas_access(
        client,
        canvas_id=canvas_id,
        user_ids=user_ids,
        channel_ids=channel_ids,
    )
    result: dict = {"id": canvas_id}
    if user_ids:
        result["user_ids"] = user_ids
    if channel_ids:
        result["channel_ids"] = channel_ids
    return {"content": json.dumps(result), "is_error": False, "files": []}


# ---------------------------------------------------------------------------
# Tool manifest
# ---------------------------------------------------------------------------

_TOOL_MANIFEST = [
    {
        "name": "canvas_create",
        "description": (
            "Create a new Slack canvas. Provide a title and markdown content. "
            "The canvas is shared in the current channel by default. "
            "Pass channel_id to share it in a different channel instead. "
            "After creating, output the permalink on its own line "
            "so Slack renders the canvas block."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Canvas title"},
                "content": {
                    "type": "string",
                    "description": "Canvas body in markdown format",
                },
                "channel_id": {
                    "type": "string",
                    "description": (
                        "Channel to share the canvas in. "
                        "Defaults to the current channel if not specified."
                    ),
                },
            },
        },
        "handler": _canvas_create,
    },
    {
        "name": "canvas_get",
        "description": (
            "Get a Slack canvas by ID. Returns the title, full markdown content, and permalink. "
            "Output the permalink on its own line so Slack renders the canvas block."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Canvas ID (e.g. F12345)"},
            },
            "required": ["id"],
        },
        "handler": _canvas_get,
    },
    {
        "name": "canvas_update",
        "description": (
            "Update a Slack canvas. Replaces the entire content and/or renames the title. "
            "Provide content to replace the body, title to rename, or both. "
            "After updating, output the permalink on its own line "
            "so Slack renders the canvas block."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Canvas ID to update"},
                "title": {
                    "type": "string",
                    "description": "New title for the canvas",
                },
                "content": {
                    "type": "string",
                    "description": "New markdown content (replaces entire canvas body)",
                },
            },
            "required": ["id"],
        },
        "handler": _canvas_update,
    },
    {
        "name": "canvas_delete",
        "description": "Permanently delete a Slack canvas.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Canvas ID to delete"},
            },
            "required": ["id"],
        },
        "handler": _canvas_delete,
    },
    {
        "name": "canvas_list",
        "description": (
            "List canvases visible to the bot. Returns canvas IDs, titles, and timestamps. "
            "Optionally filter by channel_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "Filter canvases to a specific channel",
                },
            },
        },
        "handler": _canvas_list,
    },
    {
        "name": "canvas_access_get",
        "description": (
            "Get sharing/access info for a Slack canvas. "
            "Returns which channels and users have access."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Canvas ID"},
            },
            "required": ["id"],
        },
        "handler": _canvas_access_get,
    },
    {
        "name": "canvas_access_add",
        "description": (
            "Grant access to a Slack canvas. Set access_level to read, write, or owner "
            "for specific users and/or channels."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Canvas ID"},
                "access_level": {
                    "type": "string",
                    "enum": ["read", "write", "owner"],
                    "description": "Access level to grant",
                },
                "user_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Slack user IDs to grant access to",
                },
                "channel_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Channel IDs to grant access to",
                },
            },
            "required": ["id", "access_level"],
        },
        "handler": _canvas_access_add,
    },
    {
        "name": "canvas_access_remove",
        "description": "Remove access to a Slack canvas for specific users and/or channels.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Canvas ID"},
                "user_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Slack user IDs to remove access from",
                },
                "channel_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Channel IDs to remove access from",
                },
            },
            "required": ["id"],
        },
        "handler": _canvas_access_remove,
    },
]


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class Provider(BaseToolProvider):
    """Slack canvas management tools — file-like API."""

    def __init__(self, bot_token: str, allowed_functions: list[str]):
        super().__init__(allowed_functions)
        self._client = AsyncWebClient(token=bot_token)
        self._handlers = {t["name"]: t["handler"] for t in _TOOL_MANIFEST}

    def _get_all_tools(self) -> list[dict]:
        return [
            {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
            for t in _TOOL_MANIFEST
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict,
        user_conversation_context: UserConversationContext,
        storage: BaseStorageProvider,
    ) -> ToolResult:
        handler = self._handlers.get(name)
        if not handler:
            return {"content": f"Unknown tool: {name}", "is_error": True, "files": []}
        try:
            return await handler(self._client, arguments, user_conversation_context)
        except CanvasError as e:
            return {"content": str(e), "is_error": True, "files": []}
        except Exception as e:
            logger.exception("Canvas tool call failed: %s", name)
            return {"content": f"Tool execution error: {e}", "is_error": True, "files": []}
