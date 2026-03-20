"""Tests for canvas file importer."""

from unittest.mock import AsyncMock, patch

import pytest

from slack_agents import InputFile, UserConversationContext
from slack_agents.slack.canvas_auth import CanvasAccessDenied
from slack_agents.tools.base import FileImportToolException
from slack_agents.tools.canvas_importer import MIME_SLACK_DOCS, Provider

_USER_CTX = UserConversationContext(
    user_id="U_USER",
    user_name="Test User",
    user_handle="testuser",
    channel_id="C123",
    channel_name="general",
    thread_id="1234567890.123456",
)


def _make_provider():
    return Provider(bot_token="xoxb-fake", allowed_functions=[".*"])


class TestCanvasImporterProvider:
    def test_registers_slack_docs_mime(self):
        provider = _make_provider()
        tools = provider.tools
        assert len(tools) == 1
        assert MIME_SLACK_DOCS in tools[0]["mimes"]

    def test_not_a_tool_provider(self):
        from slack_agents.tools.base import BaseToolProvider

        provider = _make_provider()
        assert not isinstance(provider, BaseToolProvider)

    async def test_import_canvas_success(self):
        provider = _make_provider()
        input_file = InputFile(
            file_bytes=b"",
            mimetype=MIME_SLACK_DOCS,
            filename="My Canvas",
            file_id="F0AMN0J4CA1",
        )
        canvas_data = {
            "title": "Project Notes",
            "content": "# Hello\n\nSome notes here.",
        }
        file_info = {
            "user": "U_USER",
        }
        with (
            patch(
                "slack_agents.tools.canvas_importer.check_canvas_access",
                new_callable=AsyncMock,
                return_value=file_info,
            ),
            patch(
                "slack_agents.tools.canvas_importer.read_canvas_content",
                new_callable=AsyncMock,
                return_value=canvas_data,
            ) as mock_read,
        ):
            result = await provider.call_tool("import_canvas", input_file, _USER_CTX, None)

        assert result["type"] == "text"
        assert "[Canvas: Project Notes]" in result["text"]
        assert "# Hello" in result["text"]
        assert "Some notes here." in result["text"]
        mock_read.assert_called_once_with(provider._client, canvas_id="F0AMN0J4CA1")

    async def test_import_canvas_no_file_id(self):
        provider = _make_provider()
        input_file = InputFile(
            file_bytes=b"",
            mimetype=MIME_SLACK_DOCS,
            filename="My Canvas",
        )
        with pytest.raises(FileImportToolException, match="no file_id"):
            await provider.call_tool("import_canvas", input_file, _USER_CTX, None)

    async def test_import_canvas_access_denied(self):
        provider = _make_provider()
        input_file = InputFile(
            file_bytes=b"",
            mimetype=MIME_SLACK_DOCS,
            filename="Secret Canvas",
            file_id="F_SECRET",
        )
        with patch(
            "slack_agents.tools.canvas_importer.check_canvas_access",
            new_callable=AsyncMock,
            side_effect=CanvasAccessDenied("You don't have read access to canvas F_SECRET"),
        ):
            with pytest.raises(FileImportToolException, match="read access"):
                await provider.call_tool("import_canvas", input_file, _USER_CTX, None)

    async def test_unknown_handler_raises(self):
        provider = _make_provider()
        input_file = InputFile(
            file_bytes=b"",
            mimetype=MIME_SLACK_DOCS,
            filename="test",
        )
        with pytest.raises(FileImportToolException, match="Unknown import handler"):
            await provider.call_tool("nonexistent", input_file, _USER_CTX, None)
