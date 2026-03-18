"""Slack streaming helpers wrapping the SDK's AsyncChatStream."""

import logging

from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)


class SlackStreamer:
    """Wraps Slack's chat streaming API for the agent loop."""

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
        self._stream = None
        self._started = False
        self._stopped = False

    @property
    def started(self) -> bool:
        return self._started

    async def _ensure_stream(self) -> None:
        if self._stream is None:
            self._stream = await self._client.chat_stream(
                channel=self._channel,
                thread_ts=self._thread_ts,
                buffer_size=128,
                recipient_team_id=self._team_id,
                recipient_user_id=self._user_id,
            )
            self._started = True

    async def send_delta(self, text: str) -> None:
        if self._stopped:
            return
        await self._ensure_stream()
        try:
            await self._stream.append(markdown_text=text)
        except Exception:
            logger.exception("Error appending to stream")

    async def send_status(self, status_text: str) -> None:
        if self._stopped:
            return
        await self._ensure_stream()
        try:
            await self._stream.append(markdown_text=f"\n_{status_text}_\n")
        except Exception:
            logger.exception("Error sending status update")

    async def stop(self, blocks: list[dict] | None = None) -> None:
        if self._stopped or self._stream is None:
            return
        self._stopped = True
        try:
            await self._stream.stop(blocks=blocks)
        except Exception:
            logger.exception("Error stopping stream")
