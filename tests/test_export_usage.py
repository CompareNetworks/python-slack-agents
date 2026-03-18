"""Tests for usage CSV export aggregation logic."""

from datetime import datetime, timezone

import pytest

from slack_agents.cli.export_usage_csv import CSV_COLUMNS, _build_row


def _make_msg(
    created_at="2026-01-15T10:00:00+00:00",
    user_id="U123",
    user_name="alice",
    user_handle="alice",
    blocks=None,
):
    return {
        "id": 1,
        "user_id": user_id,
        "user_name": user_name,
        "user_handle": user_handle,
        "created_at": created_at,
        "blocks": blocks or [],
    }


def _usage_block(
    model="claude-sonnet-4-20250514",
    version="bedrock-2025-05-14",
    input_tokens=100,
    output_tokens=50,
    cache_creation_input_tokens=10,
    cache_read_input_tokens=20,
    peak_single_call_input_tokens=100,
    estimated_cost_usd=0.01,
):
    return {
        "block_type": "usage",
        "content": {
            "model": model,
            "version": version,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens,
            "cache_read_input_tokens": cache_read_input_tokens,
            "peak_single_call_input_tokens": peak_single_call_input_tokens,
            "estimated_cost_usd": estimated_cost_usd,
        },
    }


def _tool_block():
    return {
        "block_type": "tool_use",
        "content": {
            "tool_call_id": "tc_1",
            "tool_name": "search",
            "tool_input": {},
            "tool_output": "result",
        },
    }


def _file_block():
    return {
        "block_type": "user_file",
        "content": {"data": "abc", "filename": "test.txt", "mimeType": "text/plain"},
    }


CONV = {
    "id": 42,
    "agent_name": "test-agent",
    "channel_name": "general",
    "thread_id": "1700000000.000001",
}


class TestBuildRow:
    def test_empty_messages(self):
        row = _build_row(CONV, [])
        assert row["conversation_id"] == 42
        assert row["message_count"] == 0
        assert row["date"] == ""
        assert row["started_at"] == ""
        assert row["last_message_at"] == ""
        assert row["model"] == ""
        assert row["total_input_tokens"] == 0
        assert row["estimated_cost_usd"] == 0.0

    def test_date_extraction(self):
        msgs = [_make_msg(created_at="2026-03-14T09:30:00+00:00")]
        row = _build_row(CONV, msgs)
        assert row["date"] == "2026-03-14"
        assert row["started_at"] == "2026-03-14T09:30:00+00:00"
        assert row["last_message_at"] == "2026-03-14T09:30:00+00:00"

    def test_started_at_and_last_message_at(self):
        msgs = [
            _make_msg(created_at="2026-01-01T08:00:00+00:00"),
            _make_msg(created_at="2026-01-01T09:00:00+00:00"),
            _make_msg(created_at="2026-01-01T10:00:00+00:00"),
        ]
        row = _build_row(CONV, msgs)
        assert row["started_at"] == "2026-01-01T08:00:00+00:00"
        assert row["last_message_at"] == "2026-01-01T10:00:00+00:00"
        assert row["message_count"] == 3

    def test_first_user_info(self):
        msgs = [
            _make_msg(user_id="U001", user_handle="bob"),
            _make_msg(user_id="U002", user_handle="carol"),
        ]
        row = _build_row(CONV, msgs)
        assert row["user_id"] == "U001"
        assert row["user_handle"] == "bob"

    def test_usage_sums(self):
        msgs = [
            _make_msg(
                blocks=[
                    _usage_block(input_tokens=100, output_tokens=50, estimated_cost_usd=0.01),
                ]
            ),
            _make_msg(
                blocks=[
                    _usage_block(input_tokens=200, output_tokens=80, estimated_cost_usd=0.02),
                ]
            ),
        ]
        row = _build_row(CONV, msgs)
        assert row["total_input_tokens"] == 300
        assert row["total_output_tokens"] == 130
        assert row["estimated_cost_usd"] == pytest.approx(0.03)

    def test_cache_token_sums(self):
        msgs = [
            _make_msg(
                blocks=[
                    _usage_block(cache_creation_input_tokens=10, cache_read_input_tokens=20),
                ]
            ),
            _make_msg(
                blocks=[
                    _usage_block(cache_creation_input_tokens=30, cache_read_input_tokens=40),
                ]
            ),
        ]
        row = _build_row(CONV, msgs)
        assert row["cache_creation_input_tokens"] == 40
        assert row["cache_read_input_tokens"] == 60

    def test_peak_single_call_is_max(self):
        msgs = [
            _make_msg(blocks=[_usage_block(peak_single_call_input_tokens=500)]),
            _make_msg(blocks=[_usage_block(peak_single_call_input_tokens=1200)]),
            _make_msg(blocks=[_usage_block(peak_single_call_input_tokens=800)]),
        ]
        row = _build_row(CONV, msgs)
        assert row["peak_single_call_input_tokens"] == 1200

    def test_model_version_from_first_usage(self):
        msgs = [
            _make_msg(
                blocks=[
                    _usage_block(model="claude-sonnet-4-20250514", version="v1"),
                ]
            ),
            _make_msg(
                blocks=[
                    _usage_block(model="claude-opus-4-20250514", version="v2"),
                ]
            ),
        ]
        row = _build_row(CONV, msgs)
        assert row["model"] == "claude-sonnet-4-20250514"
        assert row["version"] == "v1"

    def test_tool_call_count(self):
        msgs = [
            _make_msg(blocks=[_tool_block(), _tool_block()]),
            _make_msg(blocks=[_tool_block()]),
        ]
        row = _build_row(CONV, msgs)
        assert row["tool_call_count"] == 3

    def test_file_count(self):
        msgs = [
            _make_msg(blocks=[_file_block(), _file_block()]),
            _make_msg(blocks=[_file_block()]),
        ]
        row = _build_row(CONV, msgs)
        assert row["file_count"] == 3

    def test_conv_fields(self):
        row = _build_row(CONV, [_make_msg()])
        assert row["agent_name"] == "test-agent"
        assert row["channel_name"] == "general"
        assert row["thread_id"] == "1700000000.000001"

    def test_all_csv_columns_present(self):
        row = _build_row(CONV, [_make_msg()])
        assert set(row.keys()) == set(CSV_COLUMNS)

    def test_datetime_objects_handled(self):
        """created_at as datetime object (Postgres returns these)."""
        dt = datetime(2026, 3, 14, 9, 0, 0, tzinfo=timezone.utc)
        msgs = [_make_msg(created_at=dt)]
        row = _build_row(CONV, msgs)
        assert row["date"] == "2026-03-14"
        assert "2026-03-14" in row["started_at"]

    def test_none_cost_not_added(self):
        msgs = [
            _make_msg(blocks=[_usage_block(estimated_cost_usd=None)]),
            _make_msg(blocks=[_usage_block(estimated_cost_usd=0.05)]),
        ]
        row = _build_row(CONV, msgs)
        assert row["estimated_cost_usd"] == pytest.approx(0.05)

    def test_mixed_blocks(self):
        msgs = [
            _make_msg(
                blocks=[
                    _usage_block(input_tokens=100, output_tokens=50, estimated_cost_usd=0.01),
                    _tool_block(),
                    _file_block(),
                ]
            ),
        ]
        row = _build_row(CONV, msgs)
        assert row["total_input_tokens"] == 100
        assert row["tool_call_count"] == 1
        assert row["file_count"] == 1
