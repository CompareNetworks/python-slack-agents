"""Tests for MCP HTTP tool provider binary result extraction."""

import asyncio
import base64
import contextlib
from unittest.mock import AsyncMock, MagicMock

import anyio
import httpx
import pytest
from mcp.shared.exceptions import McpError
from mcp.types import (
    CONNECTION_CLOSED,
    BlobResourceContents,
    EmbeddedResource,
    ErrorData,
    ImageContent,
    TextContent,
)

from slack_agents.tools import mcp_http
from slack_agents.tools.mcp_http import Provider, _uri_to_filename


class TestUriToFilename:
    def test_simple_path(self):
        assert _uri_to_filename("file:///tmp/report.pdf") == "report.pdf"

    def test_http_url(self):
        assert _uri_to_filename("https://example.com/files/data.csv") == "data.csv"

    def test_encoded_characters(self):
        assert _uri_to_filename("file:///tmp/my%20file.pdf") == "my file.pdf"

    def test_no_path(self):
        assert _uri_to_filename("file:///") == "file"

    def test_plain_string(self):
        assert _uri_to_filename("report.pdf") == "report.pdf"


class TestCallToolBinaryResults:
    @pytest.fixture
    def provider(self):
        prov = Provider(url="https://example.com/mcp", allowed_functions=[".*"])
        session = AsyncMock()
        prov._tool_names.add("test_tool")
        prov._session = session
        return prov, session

    async def test_text_content(self, provider):
        prov, session = provider
        result = MagicMock()
        result.content = [TextContent(type="text", text="hello")]
        result.isError = False
        session.call_tool.return_value = result

        out = await prov.call_tool("test_tool", {}, None, None)
        assert out["content"] == "hello"
        assert out["is_error"] is False
        assert out["files"] == []

    async def test_embedded_resource_blob(self, provider):
        prov, session = provider
        blob_data = b"fake pdf content"
        blob_b64 = base64.b64encode(blob_data).decode()

        resource = BlobResourceContents(
            uri="file:///tmp/report.pdf",
            blob=blob_b64,
            mimeType="application/pdf",
        )
        embedded = EmbeddedResource(type="resource", resource=resource)

        result = MagicMock()
        result.content = [embedded]
        result.isError = False
        session.call_tool.return_value = result

        out = await prov.call_tool("test_tool", {}, None, None)
        assert len(out["files"]) == 1
        assert out["files"][0]["data"] == blob_data
        assert out["files"][0]["filename"] == "report.pdf"
        assert out["files"][0]["mimeType"] == "application/pdf"
        assert out["content"] == "(empty result)"  # no text content

    async def test_image_content(self, provider):
        prov, session = provider
        img_data = b"\x89PNG\r\n\x1a\n"
        img_b64 = base64.b64encode(img_data).decode()

        image = ImageContent(type="image", data=img_b64, mimeType="image/png")

        result = MagicMock()
        result.content = [image]
        result.isError = False
        session.call_tool.return_value = result

        out = await prov.call_tool("test_tool", {}, None, None)
        assert len(out["files"]) == 1
        assert out["files"][0]["data"] == img_data
        assert out["files"][0]["filename"] == "image.png"
        assert out["files"][0]["mimeType"] == "image/png"

    async def test_mixed_content(self, provider):
        prov, session = provider
        text = TextContent(type="text", text="Here is the file")
        blob_data = b"spreadsheet bytes"
        blob_b64 = base64.b64encode(blob_data).decode()
        resource = BlobResourceContents(
            uri="file:///data/export.xlsx",
            blob=blob_b64,
            mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        embedded = EmbeddedResource(type="resource", resource=resource)

        result = MagicMock()
        result.content = [text, embedded]
        result.isError = False
        session.call_tool.return_value = result

        out = await prov.call_tool("test_tool", {}, None, None)
        assert out["content"] == "Here is the file"
        assert len(out["files"]) == 1
        assert out["files"][0]["filename"] == "export.xlsx"

    async def test_unknown_tool(self):
        prov = Provider(url="https://example.com/mcp", allowed_functions=[".*"])
        out = await prov.call_tool("nonexistent", {}, None, None)
        assert out["is_error"] is True
        assert out["files"] == []


def _ok_result(text="ok"):
    result = MagicMock()
    result.content = [TextContent(type="text", text=text)]
    result.isError = False
    return result


class TestReconnect:
    """A closed MCP session is reopened; a call is replayed only if it never reached the server."""

    @pytest.fixture
    def provider(self):
        prov = Provider(url="https://example.com/mcp", allowed_functions=[".*"])
        prov._tool_names.add("scrape")
        prov._session = AsyncMock(name="stale")
        fresh = []

        async def connect():
            session = AsyncMock(name=f"fresh{len(fresh)}")
            session.call_tool.return_value = _ok_result()
            fresh.append(session)
            prov._session = session
            return session

        prov._connect = AsyncMock(side_effect=connect)
        return prov, fresh

    @pytest.mark.parametrize("exc", [anyio.ClosedResourceError, anyio.BrokenResourceError])
    async def test_send_on_closed_stream_reconnects_and_retries_once(self, provider, exc):
        prov, fresh = provider
        stale = prov._session
        stale.call_tool.side_effect = exc()

        out = await prov.call_tool("scrape", {"url": "u"}, None, None)

        assert out["is_error"] is False
        assert out["content"] == "ok"
        assert stale.call_tool.await_count == 1
        assert prov._connect.await_count == 1
        fresh[0].call_tool.assert_awaited_once_with(name="scrape", arguments={"url": "u"})

    async def test_retries_at_most_once(self, provider):
        prov, fresh = provider
        prov._session.call_tool.side_effect = anyio.ClosedResourceError()

        async def connect_dead():
            session = AsyncMock()
            session.call_tool.side_effect = anyio.ClosedResourceError()
            fresh.append(session)
            prov._session = session
            return session

        prov._connect.side_effect = connect_dead

        out = await prov.call_tool("scrape", {}, None, None)

        assert out["is_error"] is True
        assert prov._connect.await_count == 1
        assert fresh[0].call_tool.await_count == 1

    @pytest.mark.parametrize(
        "error",
        [
            ErrorData(code=CONNECTION_CLOSED, message="Connection closed"),
            # The SDK's error for a 404 on our session id (server restarted).
            ErrorData(code=32600, message="Session terminated"),
        ],
    )
    async def test_dead_session_after_send_is_not_replayed(self, provider, error):
        prov, fresh = provider
        stale = prov._session
        stale.call_tool.side_effect = McpError(error)

        out = await prov.call_tool("scrape", {}, None, None)

        assert out["is_error"] is True
        assert stale.call_tool.await_count == 1
        assert prov._connect.await_count == 0
        # ...but the next call gets a fresh session.
        out = await prov.call_tool("scrape", {}, None, None)
        assert out["is_error"] is False
        assert prov._connect.await_count == 1

    async def test_other_errors_keep_the_session(self, provider):
        prov, _ = provider
        stale = prov._session
        stale.call_tool.side_effect = McpError(ErrorData(code=-32603, message="boom"))

        out = await prov.call_tool("scrape", {}, None, None)

        assert out["is_error"] is True
        assert prov._session is stale
        assert prov._connect.await_count == 0

    async def test_concurrent_failures_reconnect_once(self, provider):
        prov, fresh = provider
        prov._session.call_tool.side_effect = anyio.ClosedResourceError()

        outs = await asyncio.gather(*(prov.call_tool("scrape", {}, None, None) for _ in range(3)))

        assert all(o["is_error"] is False for o in outs)
        assert prov._connect.await_count == 1
        assert fresh[0].call_tool.await_count == 3

    async def test_reconnect_failure_is_a_tool_error_and_next_call_tries_again(self, provider):
        prov, fresh = provider
        prov._session.call_tool.side_effect = anyio.ClosedResourceError()
        real_connect = prov._connect.side_effect
        prov._connect.side_effect = httpx.ConnectError("down")

        out = await prov.call_tool("scrape", {}, None, None)
        assert out["is_error"] is True
        assert prov._session is None

        prov._connect.side_effect = real_connect
        out = await prov.call_tool("scrape", {}, None, None)
        assert out["is_error"] is False


class _FakeCM:
    """Async context manager recording which task entered and exited it."""

    def __init__(self, log, name, value=None):
        self.log, self.name, self.value = log, name, value

    async def __aenter__(self):
        self.log.append(("enter", self.name, asyncio.current_task()))
        return self.value

    async def __aexit__(self, *exc):
        self.log.append(("exit", self.name, asyncio.current_task()))
        return False


class TestConnectionTask:
    @pytest.fixture
    def fakes(self, monkeypatch):
        log = []
        sessions = []

        def transport(url, http_client):
            return _FakeCM(log, "transport", (MagicMock(), MagicMock(), MagicMock()))

        def client_session(read, write):
            session = AsyncMock()
            session.list_tools.return_value = MagicMock(tools=[])
            sessions.append(session)
            return _FakeCM(log, "session", session)

        monkeypatch.setattr(mcp_http, "streamable_http_client", transport)
        monkeypatch.setattr(mcp_http.mcp, "ClientSession", client_session)
        return log, sessions

    async def test_transport_is_entered_and_exited_by_one_task(self, fakes):
        log, sessions = fakes
        prov = Provider(url="https://example.com/mcp", allowed_functions=[".*"])

        await prov.initialize()
        assert prov._session is sessions[0]
        sessions[0].list_tools.assert_awaited()  # output-schema cache filled

        # Reconnect and close from other tasks; each connection still exits in
        # the task that opened it.
        await asyncio.create_task(prov._reconnect(prov._session))
        assert prov._session is sessions[1]
        await asyncio.create_task(prov.close())
        assert prov._session is None

        enters = [t for k, n, t in log if k == "enter" and n == "transport"]
        exits = [t for k, n, t in log if k == "exit" and n == "transport"]
        assert len(enters) == len(exits) == 2
        assert enters == exits
        assert asyncio.current_task() not in enters

    async def test_connection_that_dies_drops_the_session(self, fakes):
        _, sessions = fakes
        prov = Provider(url="https://example.com/mcp", allowed_functions=[".*"])
        await prov.initialize()

        prov._conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await prov._conn_task

        assert prov._session is None
        await prov.close()
