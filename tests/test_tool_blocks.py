"""Tests for collapsible tool call block builders."""

import json

from slack_agents.slack.tool_blocks import (
    _MAX_SECTION_TEXT,
    ICON_CALLING,
    ICON_ERROR,
    ICON_SUCCESS,
    build_calling_blocks,
    build_collapsed_blocks,
    build_expanded_blocks,
)


class TestBuildCallingBlocks:
    def test_structure(self):
        blocks = build_calling_blocks("search_db")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "section"
        text = blocks[0]["text"]["text"]
        assert ICON_CALLING in text
        assert "_search_db_" in text
        assert "processing..." in text

    def test_no_accessory(self):
        blocks = build_calling_blocks("my_tool")
        assert "accessory" not in blocks[0]


class TestBuildCollapsedBlocks:
    def test_success(self):
        blocks = build_collapsed_blocks("search_db", is_error=False, tool_id="t1")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "section"
        assert ICON_SUCCESS in blocks[0]["text"]["text"]
        overflow = blocks[0]["accessory"]
        assert overflow["type"] == "overflow"
        assert overflow["action_id"] == "tool_expand_t1"
        assert overflow["options"][0]["text"]["text"] == "Show Details"

    def test_error(self):
        blocks = build_collapsed_blocks("bad_tool", is_error=True, tool_id="t2")
        assert ICON_ERROR in blocks[0]["text"]["text"]

    def test_option_value_contains_tool_info(self):
        blocks = build_collapsed_blocks("my_tool", is_error=False, tool_id="abc")
        overflow = blocks[0]["accessory"]
        value = json.loads(overflow["options"][0]["value"])
        assert value["tool_id"] == "abc"
        assert value["tool_name"] == "my_tool"


class TestBuildExpandedBlocks:
    def test_structure(self):
        blocks = build_expanded_blocks(
            "search_db",
            is_error=False,
            tool_id="t1",
            input_json='{"q": "test"}',
            output_json='["result1"]',
        )
        assert len(blocks) == 3
        types = [b["type"] for b in blocks]
        assert types == ["section", "section", "section"]

    def test_header_has_overflow_menu(self):
        blocks = build_expanded_blocks(
            "search_db",
            is_error=False,
            tool_id="t1",
            input_json="{}",
            output_json="{}",
        )
        overflow = blocks[0]["accessory"]
        assert overflow["type"] == "overflow"

    def test_input_output_content(self):
        blocks = build_expanded_blocks(
            "search_db",
            is_error=False,
            tool_id="t1",
            input_json='{"q": "test"}',
            output_json="hello",
        )
        input_text = blocks[1]["text"]["text"]
        assert '{"q": "test"}' in input_text
        assert "*Input:*" in input_text
        output_text = blocks[2]["text"]["text"]
        assert "hello" in output_text
        assert "*Output:*" in output_text

    def test_hide_option(self):
        blocks = build_expanded_blocks(
            "t",
            is_error=False,
            tool_id="t1",
            input_json="{}",
            output_json="{}",
        )
        overflow = blocks[0]["accessory"]
        assert overflow["action_id"] == "tool_collapse_t1"
        assert overflow["options"][0]["text"]["text"] == "Hide Details"

    def test_truncation(self):
        long_input = "x" * (_MAX_SECTION_TEXT + 500)
        blocks = build_expanded_blocks(
            "t",
            is_error=False,
            tool_id="t1",
            input_json=long_input,
            output_json="short",
        )
        input_text = blocks[1]["text"]["text"]
        assert "truncated" in input_text

    def test_error_icon(self):
        blocks = build_expanded_blocks(
            "t",
            is_error=True,
            tool_id="t1",
            input_json="{}",
            output_json="err",
        )
        assert ICON_ERROR in blocks[0]["text"]["text"]
