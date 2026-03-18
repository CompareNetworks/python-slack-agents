"""Abstract base class for storage providers."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone


class BaseStorageProvider(ABC):
    """Generic persistence layer.

    Implementations must provide the 6 abstract primitives (get, set, delete,
    append, get_list, query).  All higher-level domain methods have default
    implementations built on those primitives so that non-relational backends
    (Redis, DynamoDB, ...) work out of the box.

    Relational backends (PostgreSQL, SQLite) should override the domain methods
    with proper SQL for better performance.
    """

    async def initialize(self) -> None:
        """Initialize the storage backend (create tables, connect, etc.)."""

    # ------------------------------------------------------------------
    # Abstract primitives — must be implemented by every backend
    # ------------------------------------------------------------------

    @abstractmethod
    async def get(self, namespace: str, key: str) -> dict | None:
        """Get a value by namespace and key. Returns None if not found."""

    @abstractmethod
    async def set(self, namespace: str, key: str, value: dict) -> None:
        """Set a value by namespace and key (upsert)."""

    @abstractmethod
    async def delete(self, namespace: str, key: str) -> None:
        """Delete a value by namespace and key."""

    @abstractmethod
    async def append(self, namespace: str, key: str, item: dict) -> str:
        """Append an item to a list. Returns the item's ID."""

    @abstractmethod
    async def get_list(self, namespace: str, key: str) -> list[dict]:
        """Get all items in a list, ordered by insertion time."""

    @abstractmethod
    async def query(self, namespace: str, filters: dict) -> list[dict]:
        """Query items in a namespace by filters. Simple equality matching."""

    async def close(self) -> None:
        """Close connections and clean up resources."""

    # ------------------------------------------------------------------
    # Domain methods — default implementations using the primitives above.
    # Relational backends should override for efficiency.
    # ------------------------------------------------------------------

    @property
    def supports_export(self) -> bool:
        """Whether this backend supports conversation export."""
        return False

    async def get_or_create_conversation(
        self,
        agent_name: str,
        channel_id: str,
        thread_id: str,
        channel_name: str | None = None,
    ) -> int | str:
        """Get or create a conversation, return its ID."""
        key = f"{agent_name}:{channel_id}:{thread_id}"
        existing = await self.get("conversations", key)
        if existing:
            return existing["id"]
        conv_id = await self.append(
            "conversations",
            key,
            {
                "agent_name": agent_name,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "thread_id": thread_id,
            },
        )
        await self.set("conversations", key, {"id": conv_id})
        return conv_id

    async def has_conversation(self, agent_name: str, channel_id: str, thread_id: str) -> bool:
        """Check if a conversation exists for the given thread."""
        key = f"{agent_name}:{channel_id}:{thread_id}"
        return await self.get("conversations", key) is not None

    async def create_message(
        self,
        conversation_id: int | str,
        user_id: str,
        user_name: str,
        user_handle: str,
    ) -> int | str:
        """Create a new message in a conversation, return its ID."""
        return await self.append(
            "messages",
            str(conversation_id),
            {
                "conversation_id": str(conversation_id),
                "user_id": user_id,
                "user_name": user_name,
                "user_handle": user_handle,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def get_message_blocks(
        self, conversation_id: int | str
    ) -> list[tuple[int | str, list[dict]]]:
        """Return ``(message_id, blocks)`` pairs for a conversation."""
        messages_data = await self.get_list("messages", str(conversation_id))
        result: list[tuple[int | str, list[dict]]] = []
        for msg in messages_data:
            blocks = await self.get_list("blocks", str(msg["id"]))
            result.append((msg["id"], blocks))
        return result

    async def append_text_block(
        self,
        message_id: int | str,
        text: str,
        *,
        is_user: bool = False,
        source_file_id: int | str | None = None,
    ) -> None:
        await self.append(
            "blocks",
            str(message_id),
            {
                "block_type": "user_text" if is_user else "text",
                "content": {"text": text},
                "source_file_id": str(source_file_id) if source_file_id else None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
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
        return await self.append(
            "blocks",
            str(message_id),
            {
                "block_type": "user_file" if is_user else "file",
                "content": content,
                "filename": filename,
                "mimetype": mimetype,
                "size_bytes": size_bytes,
                "tool_block_id": str(tool_block_id) if tool_block_id else None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
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
        return await self.append(
            "blocks",
            str(message_id),
            {
                "block_type": "tool_use",
                "content": {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_output": tool_output,
                    "is_error": is_error,
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
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
        await self.append(
            "blocks",
            str(message_id),
            {
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
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def get_tool_call(self, tool_call_id: str) -> dict | None:
        """Look up a tool call by its LLM-generated ID.

        Default scans blocks via query(); relational backends should override
        with an indexed lookup.
        """
        rows = await self.query("blocks", {"block_type": "tool_use"})
        for row in rows:
            content = row.get("content", {})
            if isinstance(content, str):
                content = json.loads(content)
            if content.get("tool_call_id") == tool_call_id:
                return {
                    "tool_name": content["tool_name"],
                    "input_json": json.dumps(content.get("tool_input", {}), indent=2),
                    "output_json": content.get("tool_output", ""),
                    "is_error": content.get("is_error", False),
                }
        return None

    async def upsert_heartbeat(self, agent_name: str, last_ping_pong_time: float) -> None:
        """Write a heartbeat row for this agent."""
        await self.set(
            "heartbeats",
            agent_name,
            {"last_ping_pong_time": last_ping_pong_time},
        )

    async def get_heartbeat(self, agent_name: str) -> dict | None:
        """Read the heartbeat for *agent_name*.

        Returns ``{"last_ping_pong_time": <float>}`` or ``None``.
        """
        return await self.get("heartbeats", agent_name)

    async def get_conversations_for_export(
        self,
        agent_name: str,
        *,
        handle: str | None = None,
        date_from: str | datetime | None = None,
        date_to: str | datetime | None = None,
    ) -> list[dict]:
        """Find conversations for an agent, optionally filtered."""
        conversations = await self.query("conversations", {"agent_name": agent_name})
        if not handle and not date_from and not date_to:
            return conversations
        results = []
        for conv in conversations:
            messages = await self.get_list("messages", str(conv["id"]))
            if handle and not any(m.get("user_handle") == handle for m in messages):
                continue
            if date_from and not any(m.get("created_at", "") >= str(date_from) for m in messages):
                continue
            if date_to and not any(m.get("created_at", "") <= str(date_to) for m in messages):
                continue
            results.append(conv)
        return results

    async def get_messages_with_blocks(self, conversation_id: int | str) -> list[dict]:
        """Get all messages with their blocks for export purposes."""
        messages = await self.get_list("messages", str(conversation_id))
        result = []
        for msg in messages:
            blocks = await self.get_list("blocks", str(msg["id"]))
            result.append(
                {
                    "id": msg["id"],
                    "user_id": msg.get("user_id", ""),
                    "user_name": msg.get("user_name", ""),
                    "user_handle": msg.get("user_handle", ""),
                    "created_at": msg.get("created_at"),
                    "blocks": blocks,
                }
            )
        return result
