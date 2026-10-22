"""MCP over HTTP/SSE tool provider."""

import asyncio
import base64
import contextlib
import json
import logging
from urllib.parse import unquote, urlparse

import anyio
import httpx
import mcp
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED, BlobResourceContents, EmbeddedResource, ImageContent

from slack_agents import UserConversationContext
from slack_agents.llm import CHARS_PER_TOKEN
from slack_agents.storage.base import BaseStorageProvider
from slack_agents.tools.base import (
    ERROR_INPUT_ERROR,
    ERROR_SYSTEM_ERROR,
    RECOVERY_ABORT,
    RECOVERY_CONTACT_SUPPORT,
    BaseToolProvider,
    ToolResult,
    make_tool_error,
)

logger = logging.getLogger(__name__)


def _uri_to_filename(uri: str) -> str:
    """Extract a filename from an MCP resource URI."""
    parsed = urlparse(str(uri))
    path = unquote(parsed.path)
    name = path.rsplit("/", 1)[-1] if "/" in path else path
    return name or "file"


def _session_is_gone(exc: Exception) -> bool:
    """True when an MCP error means the session can never be used again.

    CONNECTION_CLOSED: the transport ended while the request was pending.
    "Session terminated" (code 32600): the server answered 404 for our session id,
    e.g. after the server restarted.
    """
    if not isinstance(exc, McpError):
        return False
    code, message = exc.error.code, exc.error.message
    return code == CONNECTION_CLOSED or (code == 32600 and message == "Session terminated")


class Provider(BaseToolProvider):
    """MCP over HTTP tool provider. Connects to a single MCP server.

    The session lives in a dedicated task that owns the transport (see
    `_run_connection`). When the connection dies the session is dropped and the
    next call reconnects; a call whose request could not even be written to the
    closed transport is retried once on the new session.
    """

    DEFAULT_INIT_RETRIES = [5, 10, 30]
    DISCONNECT_TIMEOUT = 5.0

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
        self._tool_names: set[str] = set()
        self._all_tools: list[dict] = []
        self._session: mcp.ClientSession | None = None
        self._conn_task: asyncio.Task | None = None
        self._conn_stop: asyncio.Event | None = None
        self._reconnect_lock = asyncio.Lock()

    def _get_all_tools(self) -> list[dict]:
        return self._all_tools

    async def _connect(self) -> mcp.ClientSession:
        """Open a session in a new connection task and wait until it is initialized.

        The transport's anyio task groups must be entered and exited by the same
        task, so the connection runs in its own task for its whole life instead
        of in whichever task happens to call initialize, close or reconnect.
        """
        ready: asyncio.Future[mcp.ClientSession] = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()
        task = asyncio.create_task(self._run_connection(ready, stop))
        try:
            session = await ready
        except BaseException:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
            raise
        self._session, self._conn_task, self._conn_stop = session, task, stop
        return session

    async def _run_connection(
        self, ready: "asyncio.Future[mcp.ClientSession]", stop: asyncio.Event
    ) -> None:
        try:
            async with contextlib.AsyncExitStack() as stack:
                http_client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        headers=self._headers,
                        timeout=httpx.Timeout(30.0, read=300.0),
                        follow_redirects=True,
                    )
                )
                read_stream, write_stream, _get_session_id = await stack.enter_async_context(
                    streamable_http_client(url=self._url, http_client=http_client)
                )
                session = await stack.enter_async_context(
                    mcp.ClientSession(read_stream, write_stream)
                )
                await session.initialize()
                # Populate the session's tool output-schema cache now, so call_tool
                # never sends a list_tools of its own after the tool has run.
                await session.list_tools()
                ready.set_result(session)
                await stop.wait()
        except BaseException as exc:
            if not ready.done():
                if isinstance(exc, asyncio.CancelledError):
                    ready.cancel()
                else:
                    ready.set_exception(exc)
            elif not stop.is_set():
                logger.warning("MCP %s: connection closed: %r", self._url, exc)
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            # A connection that ended on its own leaves no usable session.
            if self._conn_task is asyncio.current_task():
                self._session = None

    async def _disconnect(self) -> None:
        task, stop = self._conn_task, self._conn_stop
        self._session = self._conn_task = self._conn_stop = None
        if task is None:
            return
        stop.set()
        try:
            await asyncio.wait_for(task, self.DISCONNECT_TIMEOUT)
        except BaseException:
            logger.debug("MCP %s: error closing connection", self._url, exc_info=True)

    async def _reconnect(self, stale: mcp.ClientSession | None) -> mcp.ClientSession:
        """Replace `stale` with a fresh session. Concurrent callers share one reconnect."""
        async with self._reconnect_lock:
            if self._session is not None and self._session is not stale:
                return self._session
            logger.warning("MCP %s: reconnecting", self._url)
            await self._disconnect()
            return await self._connect()

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
            self._tool_names.add(tool.name)
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
        if tool_name not in self._tool_names:
            return make_tool_error(
                error=ERROR_INPUT_ERROR,
                code="unknown_tool",
                tool=tool_name,
                server=self._url,
                recovery=RECOVERY_ABORT,
                message=f"Tool {tool_name!r} is not available on this MCP server.",
            )

        logger.info("Calling MCP tool %s", tool_name)

        try:
            session = self._session or await self._reconnect(None)
            try:
                result = await session.call_tool(name=tool_name, arguments=arguments)
            except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                # Raised by the write to the session's closed transport stream, so
                # the request never reached the server and sending it once more on
                # a new session cannot run the tool twice. Any later failure may
                # follow execution and is never replayed.
                logger.warning("MCP %s: session closed, reconnecting for %s", self._url, tool_name)
                session = await self._reconnect(session)
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
            if _session_is_gone(e):
                # The request reached the server or may have run, so it is not
                # retried; the next call reconnects.
                self._session = None
            logger.exception("MCP tool call failed: %s", tool_name)
            return make_tool_error(
                error=ERROR_SYSTEM_ERROR,
                tool=tool_name,
                server=self._url,
                recovery=RECOVERY_CONTACT_SUPPORT,
                message=f"Tool {tool_name!r} failed: {e}",
                details={"exception": f"{type(e).__name__}: {e}"},
            )

    async def close(self) -> None:
        await self._disconnect()
        self._tool_names.clear()
        self._all_tools.clear()
