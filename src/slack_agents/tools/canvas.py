"""Built-in tool: Slack canvas management — file-like API.

Exports a Provider class that subclasses BaseToolProvider.
"""

import json
import logging

from slack_sdk.web.async_client import AsyncWebClient

from slack_agents import UserConversationContext
from slack_agents.slack.canvas_auth import CanvasAccessDenied, check_canvas_access
from slack_agents.slack.canvases import (
    CanvasError,
    create_canvas,
    delete_canvas,
    delete_canvas_access,
    edit_canvas,
    get_canvas_permalink,
    read_canvas_content,
    set_canvas_access,
)
from slack_agents.storage.base import BaseStorageProvider
from slack_agents.tools.base import BaseToolProvider, ToolResult

logger = logging.getLogger(__name__)

# Tool name → minimum access level required (None = no existing canvas)
_REQUIRED_ACCESS: dict[str, str | None] = {
    "canvas_create": None,
    "canvas_get": "read",
    "canvas_update": "write",
    "canvas_delete": "owner",
    "canvas_access_get": "read",
    "canvas_access_add": "owner",
    "canvas_access_remove": "owner",
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _canvas_create(
    client: AsyncWebClient, arguments: dict, user_conversation_context: UserConversationContext
) -> ToolResult:
    title = arguments.get("title")
    content = arguments.get("content")
    resp = await create_canvas(client, title=title, markdown=content)
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


async def _canvas_access_get(
    client: AsyncWebClient, arguments: dict, user_conversation_context: UserConversationContext
) -> ToolResult:
    canvas_id = arguments["id"]
    # file_info was already fetched during auth check; re-fetch for shares data
    from slack_agents.slack.canvases import get_canvas_info

    file_info = await get_canvas_info(client, canvas_id=canvas_id)
    shares = file_info.get("shares", {})
    access: list[dict] = []
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
    org_access = arguments.get("org_access")

    if user_ids:
        await set_canvas_access(
            client,
            canvas_id=canvas_id,
            access_level=access_level,
            user_ids=user_ids,
        )

    if org_access:
        await client.api_call(
            "canvases.access.set",
            json={
                "canvas_id": canvas_id,
                "access_level": org_access,
                "channel_ids": [],
                "user_ids": [],
            },
        )

    result: dict = {"id": canvas_id, "access_level": access_level}
    if user_ids:
        result["user_ids"] = user_ids
    if org_access:
        result["org_access"] = org_access
    return {"content": json.dumps(result), "is_error": False, "files": []}


async def _canvas_access_remove(
    client: AsyncWebClient, arguments: dict, user_conversation_context: UserConversationContext
) -> ToolResult:
    canvas_id = arguments["id"]
    user_ids = arguments.get("user_ids")
    await delete_canvas_access(
        client,
        canvas_id=canvas_id,
        user_ids=user_ids,
    )
    result: dict = {"id": canvas_id}
    if user_ids:
        result["user_ids"] = user_ids
    return {"content": json.dumps(result), "is_error": False, "files": []}


# ---------------------------------------------------------------------------
# Tool manifest
# ---------------------------------------------------------------------------

_CANVAS_DISCOVERY_HINT = (
    "IMPORTANT: Never ask the user for a canvas ID — users don't know canvas IDs. "
    "Instead, tell them to attach the canvas using the + (Attach) button "
    "in the Slack message composer, then send it to you."
)

_TOOL_MANIFEST = [
    {
        "name": "canvas_create",
        "description": (
            "Create a new Slack canvas. Provide a title and markdown content. "
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
            },
        },
        "handler": _canvas_create,
    },
    {
        "name": "canvas_get",
        "description": (
            "Get a Slack canvas by ID. Returns the title, full markdown content, "
            "and permalink. Output the permalink on its own line so Slack renders "
            "the canvas block. " + _CANVAS_DISCOVERY_HINT
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Canvas ID"},
            },
            "required": ["id"],
        },
        "handler": _canvas_get,
    },
    {
        "name": "canvas_update",
        "description": (
            "Update a Slack canvas. Replaces the entire content and/or renames "
            "the title. Provide content to replace the body, title to rename, "
            "or both. After updating, output the permalink on its own line "
            "so Slack renders the canvas block. " + _CANVAS_DISCOVERY_HINT
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
        "description": ("Permanently delete a Slack canvas. " + _CANVAS_DISCOVERY_HINT),
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
        "name": "canvas_access_get",
        "description": (
            "Get sharing/access info for a Slack canvas. "
            "Returns which channels and users have access. " + _CANVAS_DISCOVERY_HINT
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
            "Grant access to a Slack canvas. Set access_level to read, write, "
            "or owner for specific users. Optionally set org_access to grant "
            "workspace-wide access. " + _CANVAS_DISCOVERY_HINT
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
                "org_access": {
                    "type": "string",
                    "enum": ["read", "write"],
                    "description": "Set workspace-wide access level",
                },
            },
            "required": ["id", "access_level"],
        },
        "handler": _canvas_access_add,
    },
    {
        "name": "canvas_access_remove",
        "description": (
            "Remove access to a Slack canvas for specific users. " + _CANVAS_DISCOVERY_HINT
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Canvas ID"},
                "user_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Slack user IDs to remove access from",
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
    """Slack canvas management tools — file-like API with user-level authorization."""

    def __init__(self, bot_token: str, allowed_functions: list[str]):
        super().__init__(allowed_functions)
        self._client = AsyncWebClient(token=bot_token)
        self._handlers = {t["name"]: t["handler"] for t in _TOOL_MANIFEST}

    def _get_all_tools(self) -> list[dict]:
        return [
            {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
            for t in _TOOL_MANIFEST
        ]

    async def _check_user_authorization(
        self,
        tool_name: str,
        arguments: dict,
        user_conversation_context: UserConversationContext,
    ) -> None:
        """Check that the requesting user has sufficient access for this tool."""
        required = _REQUIRED_ACCESS.get(tool_name)
        if required is None:
            return
        canvas_id = arguments.get("id")
        if not canvas_id:
            return
        user_id = user_conversation_context["user_id"]
        await check_canvas_access(
            self._client,
            canvas_id=canvas_id,
            user_id=user_id,
            required_level=required,
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict,
        user_conversation_context: UserConversationContext,
        storage: BaseStorageProvider,
    ) -> ToolResult:
        handler = self._handlers.get(name)
        if not handler:
            return {
                "content": json.dumps({"error": f"Unknown tool: {name}"}),
                "is_error": True,
                "files": [],
            }
        try:
            await self._check_user_authorization(name, arguments, user_conversation_context)
            return await handler(self._client, arguments, user_conversation_context)
        except CanvasAccessDenied as e:
            return {
                "content": json.dumps({"error": "access_denied", "message": str(e)}),
                "is_error": True,
                "files": [],
            }
        except CanvasError as e:
            return {
                "content": json.dumps({"error": "canvas_error", "message": str(e)}),
                "is_error": True,
                "files": [],
            }
        except Exception as e:
            logger.exception("Canvas tool call failed: %s", name)
            return {
                "content": json.dumps({"error": "tool_error", "message": str(e)}),
                "is_error": True,
                "files": [],
            }
