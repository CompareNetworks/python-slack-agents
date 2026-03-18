"""Conversation management using the storage layer.

All storage-specific logic lives in the storage providers.
ConversationManager is a thin delegation layer that adds only
business logic (LLM message reconstruction, export guards).
"""

import logging
from datetime import datetime

from slack_agents.llm.base import Message
from slack_agents.storage.base import BaseStorageProvider

logger = logging.getLogger(__name__)


class ConversationManager:
    """Manages conversation state using a storage provider.

    Delegates all persistence to the storage provider's domain methods.
    """

    def __init__(self, storage: BaseStorageProvider):
        self._storage = storage

    @property
    def supports_export(self) -> bool:
        """Whether the underlying storage supports conversation export."""
        return self._storage.supports_export

    # --- Conversation lifecycle ---

    async def get_or_create_conversation(
        self,
        agent_name: str,
        channel_id: str,
        thread_id: str,
        channel_name: str | None = None,
    ) -> int | str:
        """Get or create a conversation, return its ID."""
        return await self._storage.get_or_create_conversation(
            agent_name, channel_id, thread_id, channel_name
        )

    async def has_conversation(self, agent_name: str, channel_id: str, thread_id: str) -> bool:
        """Check if a conversation exists for the given thread."""
        return await self._storage.has_conversation(agent_name, channel_id, thread_id)

    async def create_message(
        self,
        conversation_id: int | str,
        user_id: str,
        user_name: str,
        user_handle: str,
    ) -> int | str:
        """Create a new message in a conversation, return its ID."""
        return await self._storage.create_message(conversation_id, user_id, user_name, user_handle)

    async def get_messages(self, conversation_id: int | str) -> list[Message]:
        """Get all messages for a conversation, reconstructing LLM-format messages."""
        message_blocks = await self._storage.get_message_blocks(conversation_id)
        result: list[Message] = []
        for _message_id, blocks in message_blocks:
            result.extend(_reconstruct_messages(blocks))
        return result

    # --- Block persistence ---

    async def append_text_block(
        self,
        message_id: int | str,
        text: str,
        *,
        is_user: bool = False,
        source_file_id: int | str | None = None,
    ) -> None:
        await self._storage.append_text_block(
            message_id, text, is_user=is_user, source_file_id=source_file_id
        )

    async def append_file_block(
        self,
        message_id: int | str,
        content: dict,
        *,
        is_user: bool,
        filename: str,
        mimetype: str,
        size_bytes: int,
        tool_block_id: int | str | None = None,
    ) -> int | str:
        return await self._storage.append_file_block(
            message_id,
            content,
            is_user=is_user,
            filename=filename,
            mimetype=mimetype,
            size_bytes=size_bytes,
            tool_block_id=tool_block_id,
        )

    async def append_tool_block(
        self,
        message_id: int | str,
        *,
        tool_call_id: str,
        tool_name: str,
        tool_input: dict,
        tool_output: str,
        is_error: bool,
    ) -> int | str:
        return await self._storage.append_tool_block(
            message_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            is_error=is_error,
        )

    async def append_usage_block(
        self,
        message_id: int | str,
        *,
        model: str,
        version: str,
        input_tokens: int,
        output_tokens: int,
        cache_creation_input_tokens: int,
        cache_read_input_tokens: int,
        peak_single_call_input_tokens: int,
        estimated_cost_usd: float | None,
    ) -> None:
        await self._storage.append_usage_block(
            message_id,
            model=model,
            version=version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
            peak_single_call_input_tokens=peak_single_call_input_tokens,
            estimated_cost_usd=estimated_cost_usd,
        )

    # --- Query methods ---

    async def get_tool_call(self, tool_call_id: str) -> dict | None:
        """Look up a tool call by its LLM-generated ID."""
        return await self._storage.get_tool_call(tool_call_id)

    async def upsert_heartbeat(self, agent_name: str, last_ping_pong_time: float) -> None:
        """Write a heartbeat row for this agent."""
        await self._storage.upsert_heartbeat(agent_name, last_ping_pong_time)

    async def get_conversations_for_export(
        self,
        agent_name: str,
        *,
        handle: str | None = None,
        date_from: str | datetime | None = None,
        date_to: str | datetime | None = None,
    ) -> list[dict]:
        """Find conversations for an agent, optionally filtered by user handle and date range."""
        if not self.supports_export:
            raise NotImplementedError(
                "The current storage backend does not support conversation export. "
                "Use a file-based SQLite path or PostgreSQL."
            )
        return await self._storage.get_conversations_for_export(
            agent_name, handle=handle, date_from=date_from, date_to=date_to
        )

    async def get_messages_with_blocks(self, conversation_id: int | str) -> list[dict]:
        """Get all messages with their blocks for export purposes."""
        return await self._storage.get_messages_with_blocks(conversation_id)


def _reconstruct_messages(blocks: list[dict]) -> list[Message]:
    """Reconstruct LLM-format messages from a message's blocks.

    Block types and their mapping:
    - user_text, user_file -> grouped into a single user Message
    - text, tool_use -> grouped into assistant/user iterations
    - usage, file -> ignored (not part of LLM conversation)
    """
    messages: list[Message] = []

    # Phase 1: Build user message from user_text/user_file blocks
    user_content: list[dict] = []
    for block in blocks:
        bt = block["block_type"]
        content = block["content"]
        if bt == "user_text":
            user_content.append({"type": "text", "text": content["text"]})
        elif bt == "user_file":
            if "type" in content:
                # Already a valid API block (e.g. image stored with full structure)
                user_content.append(content)
            elif "data" in content:
                # Raw file stored as {data, filename, mimeType} — wrap as document
                user_content.append(
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": content.get("mimeType", "application/octet-stream"),
                            "data": content["data"],
                        },
                        "title": content.get("filename", ""),
                    }
                )

    if user_content:
        if len(user_content) == 1 and user_content[0].get("type") == "text":
            messages.append(Message(role="user", content=user_content[0]["text"]))
        else:
            messages.append(Message(role="user", content=user_content))

    # Phase 2: Build assistant/tool iterations from text/tool_use blocks
    assistant_blocks = [b for b in blocks if b["block_type"] in ("text", "tool_use")]

    if not assistant_blocks:
        return messages

    iterations: list[list[dict]] = []
    current: list[dict] = []

    for block in assistant_blocks:
        if block["block_type"] == "text" and current and current[-1]["block_type"] == "tool_use":
            iterations.append(current)
            current = []
        current.append(block)

    if current:
        iterations.append(current)

    for iteration in iterations:
        text_blocks = [b for b in iteration if b["block_type"] == "text"]
        tool_blocks = [b for b in iteration if b["block_type"] == "tool_use"]

        assistant_content: list[dict] = []
        for tb in text_blocks:
            assistant_content.append({"type": "text", "text": tb["content"]["text"]})
        for tub in tool_blocks:
            tc = tub["content"]
            assistant_content.append(
                {
                    "type": "tool_use",
                    "id": tc["tool_call_id"],
                    "name": tc["tool_name"],
                    "input": tc.get("tool_input", {}),
                }
            )

        if assistant_content:
            messages.append(Message(role="assistant", content=assistant_content))

        if tool_blocks:
            tool_results: list[dict] = []
            for tub in tool_blocks:
                tc = tub["content"]
                result_block = {
                    "type": "tool_result",
                    "tool_use_id": tc["tool_call_id"],
                    "content": tc.get("tool_output", ""),
                }
                if tc.get("is_error"):
                    result_block["is_error"] = True
                tool_results.append(result_block)
            messages.append(Message(role="user", content=tool_results))

    return messages
