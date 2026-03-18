"""Streaming formatter that renders markdown tables as native Slack TableBlocks."""

import logging

from slack_sdk.web.async_client import AsyncWebClient

from slack_agents.slack.format import is_table_line, table_lines_to_blocks
from slack_agents.slack.streaming import SlackStreamer
from slack_agents.slack.tool_blocks import (
    build_calling_blocks,
    build_collapsed_blocks,
)

logger = logging.getLogger(__name__)


class StreamingFormatter:
    """Streams text to Slack with native table rendering."""

    def __init__(
        self,
        client: AsyncWebClient,
        channel: str,
        thread_ts: str,
        team_id: str | None = None,
        user_id: str | None = None,
    ):
        self._client = client
        self._channel = channel
        self._thread_ts = thread_ts
        self._team_id = team_id
        self._user_id = user_id
        self._line_buffer = ""
        self._table_lines: list[str] = []
        self._in_table = False
        self._streamer: SlackStreamer | None = None
        self._has_output = False
        self._tool_messages: dict[str, str] = {}
        self._status_text: str | None = None

    async def send_delta(self, text: str) -> None:
        self._line_buffer += text
        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", 1)
            await self._process_line(line)

    async def _process_line(self, line: str) -> None:
        if is_table_line(line):
            if not self._in_table:
                await self._stop_streamer()
                self._in_table = True
            self._table_lines.append(line)
        else:
            if self._in_table:
                await self._post_table()
                self._in_table = False
                self._table_lines = []
            await self._ensure_streamer()
            await self._streamer.send_delta(line + "\n")

    async def send_status(self, status_text: str) -> None:
        await self._ensure_streamer()
        await self._streamer.send_status(status_text)

    async def stop(self) -> None:
        await self._flush_buffer()
        await self._stop_streamer()

    async def post_tool_calling(self, tool_id: str, tool_name: str, tool_input: dict) -> None:
        await self._flush_and_stop()
        try:
            resp = await self._client.chat_postMessage(
                channel=self._channel,
                thread_ts=self._thread_ts,
                text=f"Calling {tool_name}...",
                blocks=build_calling_blocks(tool_name),
            )
            self._tool_messages[tool_id] = resp["ts"]
            self._has_output = True
            await self._reapply_status()
        except Exception:
            logger.exception("Error posting tool-calling message for %s", tool_name)

    async def update_tool_done(
        self, tool_id: str, tool_name: str, tool_input: dict, tool_result: dict
    ) -> None:
        is_error = tool_result.get("is_error", False)
        blocks = build_collapsed_blocks(tool_name, is_error, tool_id)
        fallback = f"Tool {tool_name} {'failed' if is_error else 'complete'}."
        ts = self._tool_messages.get(tool_id)
        try:
            if ts:
                await self._client.chat_update(
                    channel=self._channel,
                    ts=ts,
                    text=fallback,
                    blocks=blocks,
                )
            else:
                await self._client.chat_postMessage(
                    channel=self._channel,
                    thread_ts=self._thread_ts,
                    text=fallback,
                    blocks=blocks,
                )
                self._has_output = True
            await self._reapply_status()
        except Exception:
            logger.exception("Error updating tool-done message for %s", tool_name)

    def set_status(self, status_text: str) -> None:
        self._status_text = status_text

    async def _reapply_status(self) -> None:
        if not self._status_text:
            return
        try:
            await self._client.assistant_threads_setStatus(
                channel_id=self._channel,
                thread_ts=self._thread_ts,
                status=self._status_text,
            )
        except Exception:
            pass

    async def _ensure_streamer(self) -> None:
        if self._streamer is None:
            self._streamer = SlackStreamer(
                self._client, self._channel, self._thread_ts, self._team_id, self._user_id
            )

    async def _flush_and_stop(self) -> None:
        await self._flush_buffer()
        await self._stop_streamer()

    async def _flush_buffer(self) -> None:
        if self._line_buffer:
            remaining = self._line_buffer
            self._line_buffer = ""
            if self._in_table and is_table_line(remaining):
                self._table_lines.append(remaining)
            elif self._in_table:
                await self._post_table()
                self._in_table = False
                self._table_lines = []
                await self._ensure_streamer()
                await self._streamer.send_delta(remaining)
            else:
                await self._ensure_streamer()
                await self._streamer.send_delta(remaining)

        if self._in_table and self._table_lines:
            await self._post_table()
            self._in_table = False
            self._table_lines = []

    async def _stop_streamer(self) -> None:
        if self._streamer is not None and self._streamer.started:
            await self._streamer.stop()
            self._has_output = True
            await self._reapply_status()
        self._streamer = None

    async def _post_table(self) -> None:
        if not self._table_lines:
            return
        block = table_lines_to_blocks(self._table_lines)
        try:
            await self._client.chat_postMessage(
                channel=self._channel,
                thread_ts=self._thread_ts,
                blocks=[block],
                text="(table)",
            )
            self._has_output = True
            await self._reapply_status()
        except Exception:
            logger.exception("Error posting table block")

    @property
    def has_output(self) -> bool:
        return self._has_output
