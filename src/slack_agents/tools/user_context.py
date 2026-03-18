"""Built-in tool: per-user memory backed by Slack canvases.

Each user gets a lazily-created canvas that stores their preferences and
context across conversations.  The LLM checks it at conversation start and
offers to save important context.

Exports a Provider class that subclasses BaseToolProvider.
"""

import json
import logging

from slack_sdk.web.async_client import AsyncWebClient

from slack_agents import UserConversationContext
from slack_agents.llm import CHARS_PER_TOKEN
from slack_agents.slack.canvases import (
    CanvasError,
    create_canvas,
    edit_canvas,
    get_canvas_permalink,
    read_canvas_content,
    set_canvas_access,
)
from slack_agents.storage.base import BaseStorageProvider
from slack_agents.tools.base import BaseToolProvider, ToolResult

logger = logging.getLogger(__name__)

NAMESPACE = "user_context_canvas"


# ---------------------------------------------------------------------------
# Tool manifest
# ---------------------------------------------------------------------------

_TOOL_MANIFEST = [
    {
        "name": "get_user_context",
        "description": (
            "IMPORTANT: You MUST call this tool at the very start of every conversation, "
            "before responding to the user's first message. It loads the user's saved "
            "preferences and context from previous conversations. Do NOT skip this step. "
            "Returns the user's saved context content (markdown) and a permalink to their canvas, "
            "or empty content if nothing has been saved yet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "set_user_context",
        "description": (
            "Save or update the current user's preferences and context for future conversations. "
            "Use this when the user shares preferences, corrections, or context worth remembering "
            "across conversations. Always confirm with the user before saving. "
            "This replaces the entire saved context — "
            "include all existing context you want to keep. "
            "After saving, output the permalink on its own line "
            "so Slack renders the canvas block."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "The name of this agent (used in the canvas title)",
                },
                "content": {
                    "type": "string",
                    "description": "The full user context to save, in markdown format",
                },
            },
            "required": ["agent_name", "content"],
        },
    },
]


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class Provider(BaseToolProvider):
    """Per-user memory tool backed by Slack canvases."""

    def __init__(
        self,
        bot_token: str,
        allowed_functions: list[str],
        max_tokens: int = 1000,
    ):
        super().__init__(allowed_functions)
        self._client = AsyncWebClient(token=bot_token)
        self._max_tokens = max_tokens
        self._bot_user_id: str | None = None

    async def initialize(self) -> None:
        resp = await self._client.auth_test()
        self._bot_user_id = resp["user_id"]

    def _storage_key(self, user_id: str) -> str:
        return f"{self._bot_user_id}:{user_id}"

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
        try:
            if name == "get_user_context":
                return await self._get_user_context(user_conversation_context, storage)
            elif name == "set_user_context":
                return await self._set_user_context(arguments, user_conversation_context, storage)
            else:
                err = {"error": f"Unknown tool: {name}"}
                return {"content": json.dumps(err), "is_error": True, "files": []}
        except CanvasError as e:
            return {"content": json.dumps({"error": str(e)}), "is_error": True, "files": []}
        except Exception as e:
            logger.exception("User context tool call failed: %s", name)
            err = {"error": f"Tool execution error: {e}"}
            return {"content": json.dumps(err), "is_error": True, "files": []}

    async def _get_user_context(
        self,
        user_conversation_context: UserConversationContext,
        storage: BaseStorageProvider,
    ) -> ToolResult:
        user_id = user_conversation_context["user_id"]
        key = self._storage_key(user_id)

        record = await storage.get(NAMESPACE, key)
        if not record:
            return {"content": json.dumps({"content": ""}), "is_error": False, "files": []}

        canvas_id = record["canvas_id"]
        try:
            data = await read_canvas_content(self._client, canvas_id=canvas_id)
        except Exception:
            # Canvas was deleted or is inaccessible — clear the mapping and return empty
            logger.debug("Canvas %s unreadable, clearing mapping", canvas_id)
            await storage.delete(NAMESPACE, key)
            return {"content": json.dumps({"content": ""}), "is_error": False, "files": []}

        result: dict = {"content": data.get("content", "")}
        permalink = await get_canvas_permalink(self._client, canvas_id=canvas_id)
        if permalink:
            result["permalink"] = permalink

        return {"content": json.dumps(result), "is_error": False, "files": []}

    async def _set_user_context(
        self,
        arguments: dict,
        user_conversation_context: UserConversationContext,
        storage: BaseStorageProvider,
    ) -> ToolResult:
        agent_name = arguments["agent_name"]
        content = arguments["content"]
        user_id = user_conversation_context["user_id"]
        user_name = user_conversation_context["user_name"]
        key = self._storage_key(user_id)

        # Check token limit
        if len(content) // CHARS_PER_TOKEN > self._max_tokens:
            return {
                "content": json.dumps(
                    {
                        "error": (
                            f"Content too long: ~{len(content) // 4} tokens "
                            f"exceeds the {self._max_tokens} token limit. "
                            "Please shorten the content."
                        ),
                    }
                ),
                "is_error": True,
                "files": [],
            }

        record = await storage.get(NAMESPACE, key)
        canvas_id = record["canvas_id"] if record else None

        # Try to update existing canvas
        if canvas_id:
            try:
                await edit_canvas(
                    self._client,
                    canvas_id=canvas_id,
                    changes=[
                        {
                            "operation": "replace",
                            "document_content": {"type": "markdown", "markdown": content},
                        }
                    ],
                )
            except Exception:
                # Canvas was deleted or is inaccessible — create a new one
                canvas_id = None

        # Create new canvas if needed
        if not canvas_id:
            title = f"{agent_name} ({user_name})"
            resp = await create_canvas(self._client, title=title, markdown=content)
            canvas_id = resp.get("canvas_id", "unknown")

            # Grant the user write access
            try:
                await set_canvas_access(
                    self._client,
                    canvas_id=canvas_id,
                    access_level="write",
                    user_ids=[user_id],
                )
            except CanvasError:
                logger.warning(
                    "Could not grant write access to user %s on canvas %s",
                    user_id,
                    canvas_id,
                )

            await storage.set(NAMESPACE, key, {"canvas_id": canvas_id})

        result: dict = {"canvas_id": canvas_id, "saved": True}
        permalink = await get_canvas_permalink(self._client, canvas_id=canvas_id)
        if permalink:
            result["permalink"] = permalink

        return {"content": json.dumps(result), "is_error": False, "files": []}
