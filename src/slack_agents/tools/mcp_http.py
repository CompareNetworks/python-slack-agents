"""MCP over HTTP/SSE tool provider."""

import asyncio
import base64
import contextlib
import json
import logging
from urllib.parse import unquote, urlparse

import httpx
import mcp
from mcp.client.streamable_http import streamable_http_client
from mcp.types import BlobResourceContents, EmbeddedResource, ImageContent

from slack_agents import UserConversationContext
from slack_agents.llm import CHARS_PER_TOKEN
from slack_agents.storage.base import BaseStorageProvider
from slack_agents.tools.base import BaseToolProvider, ToolResult

logger = logging.getLogger(__name__)


def _uri_to_filename(uri: str) -> str:
    """Extract a filename from an MCP resource URI."""
    parsed = urlparse(str(uri))
    path = unquote(parsed.path)
    name = path.rsplit("/", 1)[-1] if "/" in path else path
    return name or "file"


class Provider(BaseToolProvider):
    """MCP over HTTP tool provider. Connects to a single MCP server."""

    DEFAULT_INIT_RETRIES = [5, 10, 30]

    def __init__(
        self,
        url: str,
        allowed_functions: list[str],
        headers: dict | None = None,
        init_retries: list[int | float] | None = None,
    ):
        super().__init__(allowed_functions)
        self._url = url
        self._headers = headers or {}
        self._init_retries = init_retries if init_retries is not None else self.DEFAULT_INIT_RETRIES
        self._tool_map: dict[str, mcp.ClientSession] = {}
        self._all_tools: list[dict] = []
        self._session: mcp.ClientSession | None = None
        self._exit_stack: contextlib.AsyncExitStack | None = None

    def _get_all_tools(self) -> list[dict]:
        return self._all_tools

    async def _connect(self) -> None:
        """Establish connection to the MCP server."""
        http_client = httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(30.0, read=300.0),
            follow_redirects=True,
        )

        stack = contextlib.AsyncExitStack()
        self._exit_stack = stack

        await stack.enter_async_context(http_client)
        read_stream, write_stream, _get_session_id = await stack.enter_async_context(
            streamable_http_client(url=self._url, http_client=http_client)
        )

        session = mcp.ClientSession(read_stream, write_stream)
        await stack.enter_async_context(session)
        await session.initialize()
        self._session = session

    async def initialize(self) -> None:
        """Connect to the MCP server and discover tools, retrying on connection errors."""
        max_attempts = 1 + len(self._init_retries)
        for attempt in range(1, max_attempts + 1):
            try:
                await self._connect()
                break
            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                OSError,
                asyncio.CancelledError,
            ) as exc:
                if attempt == max_attempts:
                    logger.error(
                        "MCP %s: failed after %d attempts: %s", self._url, max_attempts, exc
                    )
                    raise
                backoff = self._init_retries[attempt - 1]
                logger.warning(
                    "MCP %s: connection attempt %d/%d failed (%s), retrying in %gs",
                    self._url,
                    attempt,
                    max_attempts,
                    exc,
                    backoff,
                )
                # Clean up partial state before retrying
                if self._exit_stack:
                    with contextlib.suppress(BaseException):
                        await self._exit_stack.aclose()
                    self._exit_stack = None
                await asyncio.sleep(backoff)

        tools_result = await self._session.list_tools()

        server_tokens = 0
        for tool in tools_result.tools:
            tool_def = {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
            }
            tool_tokens = len(json.dumps(tool_def)) // CHARS_PER_TOKEN
            server_tokens += tool_tokens
            self._tool_map[tool.name] = self._session
            self._all_tools.append(tool_def)

        # Log filtered tools
        allowed = self.tools
        filtered_count = len(self._all_tools) - len(allowed)
        if filtered_count:
            logger.info(
                "MCP %s: %d tools loaded, %d filtered out, ~%d tokens",
                self._url,
                len(allowed),
                filtered_count,
                server_tokens,
            )
        else:
            logger.info(
                "MCP %s: %d tools loaded, ~%d tokens",
                self._url,
                len(allowed),
                server_tokens,
            )

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict,
        user_conversation_context: UserConversationContext,
        storage: BaseStorageProvider,
    ) -> ToolResult:
        """Execute a tool call and return the result."""
        session = self._tool_map.get(tool_name)
        if not session:
            return {"content": f"Unknown tool: {tool_name}", "is_error": True, "files": []}

        logger.info("Calling MCP tool %s", tool_name)

        try:
            result = await session.call_tool(name=tool_name, arguments=arguments)
            text_parts = []
            files = []

            for content in result.content:
                if isinstance(content, EmbeddedResource) and isinstance(
                    content.resource, BlobResourceContents
                ):
                    data = base64.b64decode(content.resource.blob)
                    filename = _uri_to_filename(content.resource.uri)
                    mime = content.resource.mimeType or "application/octet-stream"
                    files.append({"data": data, "filename": filename, "mimeType": mime})
                elif isinstance(content, ImageContent):
                    data = base64.b64decode(content.data)
                    ext = content.mimeType.split("/")[-1] if content.mimeType else "png"
                    files.append(
                        {
                            "data": data,
                            "filename": f"image.{ext}",
                            "mimeType": content.mimeType,
                        }
                    )
                elif hasattr(content, "text"):
                    text_parts.append(content.text)
                else:
                    text_parts.append(str(content))

            return {
                "content": "\n".join(text_parts) if text_parts else "(empty result)",
                "is_error": bool(result.isError),
                "files": files,
            }
        except Exception as e:
            logger.exception("MCP tool call failed: %s", tool_name)
            return {"content": f"Tool execution error: {e}", "is_error": True, "files": []}

    async def close(self) -> None:
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception:
                logger.exception("Error closing MCP connection")
            self._exit_stack = None
            self._session = None
            self._tool_map.clear()
            self._all_tools.clear()
