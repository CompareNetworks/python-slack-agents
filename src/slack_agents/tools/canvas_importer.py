"""Built-in file importer: Slack canvas (application/vnd.slack-docs).

Exports a Provider class that subclasses BaseFileImporterProvider.
When a canvas is attached to a message, this importer reads its content
via the Slack API and returns the markdown as a TextBlock.
"""

import logging

from slack_sdk.web.async_client import AsyncWebClient

from slack_agents import InputFile, UserConversationContext
from slack_agents.slack.canvas_auth import CanvasAccessDenied, check_canvas_access
from slack_agents.slack.canvases import CanvasError, read_canvas_content
from slack_agents.storage.base import BaseStorageProvider
from slack_agents.tools.base import BaseFileImporterProvider, ContentBlock, FileImportToolException

logger = logging.getLogger(__name__)

MIME_SLACK_DOCS = "application/vnd.slack-docs"


async def _import_canvas(
    client: AsyncWebClient,
    f: InputFile,
    user_conversation_context: UserConversationContext,
) -> ContentBlock:
    """Read a canvas attachment and return its content as a TextBlock."""
    canvas_id = f.get("file_id")
    if not canvas_id:
        raise FileImportToolException(
            f"Canvas file '{f['filename']}' has no file_id — cannot read content"
        )

    user_id = user_conversation_context["user_id"]
    try:
        await check_canvas_access(
            client,
            canvas_id=canvas_id,
            user_id=user_id,
            required_level="read",
        )
    except CanvasAccessDenied as exc:
        raise FileImportToolException(str(exc)) from exc

    try:
        data = await read_canvas_content(client, canvas_id=canvas_id)
    except CanvasError as exc:
        raise FileImportToolException(f"Failed to read canvas {canvas_id}: {exc}") from exc

    title = data.get("title", "")
    content = data.get("content", "")
    header = f"[Canvas: {title}]" if title else f"[Canvas: {canvas_id}]"
    return {"type": "text", "text": f"{header}\n\n{content}"}


_HANDLER_MANIFEST = [
    {
        "name": "import_canvas",
        "mimes": {MIME_SLACK_DOCS},
        "max_size": 10_000_000,
        "handler": _import_canvas,
    },
]


class Provider(BaseFileImporterProvider):
    """Canvas file importer — reads attached canvases via the Slack API."""

    def __init__(self, bot_token: str, allowed_functions: list[str], **kwargs):
        super().__init__(allowed_functions, **kwargs)
        self._client = AsyncWebClient(token=bot_token)
        self._handler_map = {h["name"]: h["handler"] for h in _HANDLER_MANIFEST}

    def _get_all_tools(self) -> list[dict]:
        return _HANDLER_MANIFEST

    async def call_tool(
        self,
        name: str,
        arguments: dict,
        user_conversation_context: UserConversationContext,
        storage: BaseStorageProvider,
    ) -> ContentBlock:
        handler = self._handler_map.get(name)
        if not handler:
            raise FileImportToolException(f"Unknown import handler: {name}")
        return await handler(self._client, arguments, user_conversation_context)
