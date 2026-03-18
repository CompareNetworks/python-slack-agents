"""Tests for MCP HTTP tool provider binary result extraction."""

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import BlobResourceContents, EmbeddedResource, ImageContent, TextContent

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
        prov._tool_map["test_tool"] = session
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
