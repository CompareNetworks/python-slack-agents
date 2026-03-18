"""Slack file download and upload utilities."""

import logging

import httpx
from slack_sdk.web.async_client import AsyncWebClient

from slack_agents import UserConversationContext
from slack_agents.files import FileHandlerRegistry
from slack_agents.storage.base import BaseStorageProvider

logger = logging.getLogger(__name__)


async def download_file(url: str, bot_token: str) -> bytes:
    """Download a file from Slack using the bot token for auth."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            headers={"Authorization": f"Bearer {bot_token}"},
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.content


async def process_files_for_message(
    files: list[dict],
    bot_token: str,
    registry: FileHandlerRegistry,
    user_conversation_context: UserConversationContext,
    storage: BaseStorageProvider,
) -> list[tuple[dict, dict]]:
    """Process Slack file attachments into content blocks for the LLM."""
    results: list[tuple[dict, dict]] = []
    for file_info in files:
        mimetype = file_info.get("mimetype", "")
        filename = file_info.get("name", "unknown")
        url = file_info.get("url_private_download") or file_info.get("url_private")

        if not url:
            continue

        try:
            file_bytes = await download_file(url, bot_token)
        except Exception:
            logger.exception("Failed to download file: %s", filename)
            results.append(
                (
                    {
                        "type": "text",
                        "text": f"[File: {filename} — download failed]",
                    },
                    {"filename": filename, "mimetype": mimetype, "size_bytes": 0},
                )
            )
            continue

        meta = {
            "filename": filename,
            "mimetype": mimetype,
            "size_bytes": len(file_bytes),
        }

        block = await registry.process_file(
            file_bytes, mimetype, filename, user_conversation_context, storage
        )
        if block is not None:
            if block.get("type") != "image":
                meta["raw_bytes"] = file_bytes
            results.append((block, meta))
        else:
            meta["raw_bytes"] = file_bytes
            results.append(
                (
                    {
                        "type": "text",
                        "text": f"[File: {filename} — could not extract content]",
                    },
                    meta,
                )
            )

    return results


async def upload_file(
    client: AsyncWebClient,
    channel: str,
    thread_ts: str,
    content: str | bytes,
    filename: str,
    title: str | None = None,
) -> None:
    """Upload a file to Slack using files.upload_v2."""
    await client.files_upload_v2(
        channel=channel,
        thread_ts=thread_ts,
        content=content,
        filename=filename,
        title=title or filename,
    )
