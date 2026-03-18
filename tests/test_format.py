"""Tests for Slack table/markdown formatting utilities."""

from slack_agents.slack.format import (
    is_separator_line,
    is_table_line,
    table_lines_to_blocks,
)


def test_is_table_line_positive():
    assert is_table_line("| a | b |")
    assert is_table_line("  | a | b |")


def test_is_table_line_negative():
    assert not is_table_line("hello world")
    assert not is_table_line("")
    assert not is_table_line("some | pipe | inside")


def test_is_separator_line():
    assert is_separator_line("| --- | --- |")
    assert is_separator_line("| :---: | ---: |")
    assert not is_separator_line("| data | data |")
    assert not is_separator_line("not a table")


def test_table_lines_to_blocks_basic():
    lines = [
        "| Name | Age |",
        "| --- | --- |",
        "| Alice | 30 |",
        "| Bob | 25 |",
    ]
    result = table_lines_to_blocks(lines)
    assert result["type"] == "table"
    # Should have 3 rows (header + 2 data, separator filtered out)
    assert len(result["rows"]) == 3
    assert result["rows"][0][0]["text"] == "Name"
    assert result["rows"][1][0]["text"] == "Alice"
    assert result["rows"][2][1]["text"] == "25"


def test_table_lines_to_blocks_strips_markdown():
    lines = [
        "| **bold** | *italic* |",
        "| `code` | ~~strike~~ |",
    ]
    result = table_lines_to_blocks(lines)
    assert result["rows"][0][0]["text"] == "bold"
    assert result["rows"][0][1]["text"] == "italic"
    assert result["rows"][1][0]["text"] == "code"
    assert result["rows"][1][1]["text"] == "strike"


def test_table_lines_to_blocks_empty():
    result = table_lines_to_blocks([])
    assert result["rows"] == []


def test_table_lines_to_blocks_pads_short_rows():
    lines = [
        "| a | b | c |",
        "| x |",
    ]
    result = table_lines_to_blocks(lines)
    # Second row should be padded to 3 columns
    assert len(result["rows"][1]) == 3
    # Empty cells become " " (Slack requires non-empty text)
    assert result["rows"][1][1]["text"] == " "
