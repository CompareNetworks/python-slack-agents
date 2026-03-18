"""SQLite storage provider using aiosqlite."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import aiosqlite

from slack_agents.storage.base import BaseStorageProvider

logger = logging.getLogger(__name__)

SCHEMA_SQL = (Path(__file__).parent / "sqlite.sql").read_text()


class Provider(BaseStorageProvider):
    """SQLite-backed storage. Use path=':memory:' for in-memory or a file path for persistence."""

    def __init__(self, path: str):
        self._path = path
        self._db: aiosqlite.Connection | None = None

    @property
    def persistent(self) -> bool:
        return self._path != ":memory:"

    @property
    def supports_export(self) -> bool:
        return self.persistent

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()
        logger.info("SQLite storage initialized (path=%s)", self._path)

    # ------------------------------------------------------------------
    # Abstract primitives (kv_store / list_store)
    # ------------------------------------------------------------------

    async def get(self, namespace: str, key: str) -> dict | None:
        async with self._db.execute(
            "SELECT value FROM kv_store WHERE namespace=? AND key=?",
            (namespace, key),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return json.loads(row[0])
            return None

    async def set(self, namespace: str, key: str, value: dict) -> None:
        value_json = json.dumps(value)
        await self._db.execute(
            "INSERT INTO kv_store (namespace, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT (namespace, key) DO UPDATE SET value=excluded.value",
            (namespace, key, value_json),
        )
        await self._db.commit()

    async def delete(self, namespace: str, key: str) -> None:
        await self._db.execute(
            "DELETE FROM kv_store WHERE namespace=? AND key=?",
            (namespace, key),
        )
        await self._db.commit()

    async def append(self, namespace: str, key: str, item: dict) -> str:
        item_json = json.dumps(item)
        async with self._db.execute(
            "INSERT INTO list_store (namespace, key, value) VALUES (?, ?, ?)",
            (namespace, key, item_json),
        ) as cursor:
            row_id = cursor.lastrowid
        await self._db.commit()
        return str(row_id)

    async def get_list(self, namespace: str, key: str) -> list[dict]:
        async with self._db.execute(
            "SELECT id, value FROM list_store WHERE namespace=? AND key=? ORDER BY id",
            (namespace, key),
        ) as cursor:
            rows = await cursor.fetchall()
        result = []
        for row in rows:
            val = json.loads(row[1])
            val["id"] = str(row[0])
            result.append(val)
        return result

    async def query(self, namespace: str, filters: dict) -> list[dict]:
        results = []
        # Search list_store
        async with self._db.execute(
            "SELECT id, value FROM list_store WHERE namespace=? ORDER BY id",
            (namespace,),
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            val = json.loads(row[1])
            val["id"] = str(row[0])
            if all(val.get(k) == v for k, v in filters.items()):
                results.append(val)
        # Search kv_store
        async with self._db.execute(
            "SELECT value FROM kv_store WHERE namespace=?",
            (namespace,),
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            val = json.loads(row[0])
            if all(val.get(k) == v for k, v in filters.items()):
                results.append(val)
        return results

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ------------------------------------------------------------------
    # Domain method overrides — relational SQL
    # ------------------------------------------------------------------

    async def get_or_create_conversation(
        self,
        agent_name: str,
        channel_id: str,
        thread_id: str,
        channel_name: str | None = None,
    ) -> int | str:
        async with self._db.execute(
            "SELECT id FROM conversations WHERE agent_name=? AND channel_id=? AND thread_id=?",
            (agent_name, channel_id, thread_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            conv_id = row[0]
            if channel_name:
                await self._db.execute(
                    "UPDATE conversations SET channel_name=? WHERE id=?",
                    (channel_name, conv_id),
                )
                await self._db.commit()
            return conv_id
        await self._db.execute(
            "INSERT INTO conversations (agent_name, channel_id, channel_name, thread_id) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (agent_name, channel_id, thread_id) "
            "DO UPDATE SET channel_name=excluded.channel_name",
            (agent_name, channel_id, channel_name, thread_id),
        )
        await self._db.commit()
        async with self._db.execute(
            "SELECT id FROM conversations WHERE agent_name=? AND channel_id=? AND thread_id=?",
            (agent_name, channel_id, thread_id),
        ) as cursor:
            row = await cursor.fetchone()
        return row[0]

    async def has_conversation(self, agent_name: str, channel_id: str, thread_id: str) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM conversations WHERE agent_name=? AND channel_id=? AND thread_id=?",
            (agent_name, channel_id, thread_id),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def create_message(
        self,
        conversation_id: int | str,
        user_id: str,
        user_name: str,
        user_handle: str,
    ) -> int | str:
        async with self._db.execute(
            "INSERT INTO messages (conversation_id, user_id, user_name, user_handle) "
            "VALUES (?, ?, ?, ?)",
            (int(conversation_id), user_id, user_name, user_handle),
        ) as cursor:
            msg_id = cursor.lastrowid
        await self._db.commit()
        return msg_id

    async def get_message_blocks(
        self, conversation_id: int | str
    ) -> list[tuple[int | str, list[dict]]]:
        async with self._db.execute(
            "SELECT id FROM messages WHERE conversation_id=? ORDER BY id",
            (int(conversation_id),),
        ) as cursor:
            msg_rows = await cursor.fetchall()

        result: list[tuple[int | str, list[dict]]] = []
        for msg_row in msg_rows:
            msg_id = msg_row[0]
            async with self._db.execute(
                "SELECT id, block_type, content, is_user, source_file_id, "
                "tool_block_id, filename, mimetype, size_bytes, "
                "tool_call_id, tool_name, tool_input, tool_output, is_error, "
                "created_at FROM blocks WHERE message_id=? ORDER BY id",
                (msg_id,),
            ) as cursor:
                block_rows = await cursor.fetchall()

            blocks = []
            for br in block_rows:
                block_type = br[1]
                content = json.loads(br[2])
                blocks.append({"block_type": block_type, "content": content})
            result.append((msg_id, blocks))
        return result

    async def append_text_block(
        self,
        message_id: int | str,
        text: str,
        *,
        is_user: bool = False,
        source_file_id: int | str | None = None,
    ) -> None:
        content_json = json.dumps({"text": text})
        await self._db.execute(
            "INSERT INTO blocks (message_id, block_type, content, is_user, source_file_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                int(message_id),
                "user_text" if is_user else "text",
                content_json,
                is_user,
                int(source_file_id) if source_file_id else None,
            ),
        )
        await self._db.commit()

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
        content_json = json.dumps(content)
        async with self._db.execute(
            "INSERT INTO blocks "
            "(message_id, block_type, content, is_user, filename, mimetype, "
            "size_bytes, tool_block_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(message_id),
                "user_file" if is_user else "file",
                content_json,
                is_user,
                filename,
                mimetype,
                size_bytes,
                int(tool_block_id) if tool_block_id else None,
            ),
        ) as cursor:
            block_id = cursor.lastrowid
        await self._db.commit()
        return block_id

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
        content_json = json.dumps(
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_output": tool_output,
                "is_error": is_error,
            }
        )
        input_json = json.dumps(tool_input)
        async with self._db.execute(
            "INSERT INTO blocks "
            "(message_id, block_type, content, tool_call_id, tool_name, "
            "tool_input, tool_output, is_error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(message_id),
                "tool_use",
                content_json,
                tool_call_id,
                tool_name,
                input_json,
                tool_output,
                is_error,
            ),
        ) as cursor:
            block_id = cursor.lastrowid
        await self._db.commit()
        return block_id

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
        content_json = json.dumps(
            {
                "model": model,
                "version": version,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": cache_creation_input_tokens,
                "cache_read_input_tokens": cache_read_input_tokens,
                "peak_single_call_input_tokens": peak_single_call_input_tokens,
                "estimated_cost_usd": estimated_cost_usd,
            }
        )
        await self._db.execute(
            "INSERT INTO blocks (message_id, block_type, content) VALUES (?, ?, ?)",
            (int(message_id), "usage", content_json),
        )
        await self._db.commit()

    async def get_tool_call(self, tool_call_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT tool_name, tool_input, tool_output, is_error FROM blocks WHERE tool_call_id=?",
            (tool_call_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return {
            "tool_name": row[0],
            "input_json": json.dumps(json.loads(row[1]) if row[1] else {}, indent=2),
            "output_json": row[2] or "",
            "is_error": bool(row[3]),
        }

    async def upsert_heartbeat(self, agent_name: str, last_ping_pong_time: float) -> None:
        await self._db.execute(
            "INSERT INTO agent_heartbeats (agent_name, last_ping_pong_time) "
            "VALUES (?, ?) "
            "ON CONFLICT (agent_name) DO UPDATE SET "
            "last_ping_pong_time=excluded.last_ping_pong_time",
            (agent_name, last_ping_pong_time),
        )
        await self._db.commit()

    async def get_heartbeat(self, agent_name: str) -> dict | None:
        async with self._db.execute(
            "SELECT last_ping_pong_time FROM agent_heartbeats WHERE agent_name=?",
            (agent_name,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            # Fall back to kv_store for backwards compatibility
            return await self.get("heartbeats", agent_name)
        return {"last_ping_pong_time": row[0]}

    async def get_conversations_for_export(
        self,
        agent_name: str,
        *,
        handle: str | None = None,
        date_from: str | datetime | None = None,
        date_to: str | datetime | None = None,
    ) -> list[dict]:
        conditions = ["c.agent_name = ?"]
        params: list = [agent_name]

        needs_join = handle is not None or date_from is not None or date_to is not None

        if handle is not None:
            conditions.append("m.user_handle = ?")
            params.append(handle)
        if date_from is not None:
            conditions.append("m.created_at >= ?")
            params.append(str(date_from))
        if date_to is not None:
            conditions.append("m.created_at <= ?")
            params.append(str(date_to))

        where = " AND ".join(conditions)
        if needs_join:
            sql = (
                f"SELECT DISTINCT c.id, c.agent_name, c.channel_id, "
                f"c.channel_name, c.thread_id "
                f"FROM conversations c "
                f"JOIN messages m ON m.conversation_id = c.id "
                f"WHERE {where} ORDER BY c.id"
            )
        else:
            sql = (
                f"SELECT c.id, c.agent_name, c.channel_id, "
                f"c.channel_name, c.thread_id "
                f"FROM conversations c WHERE {where} ORDER BY c.id"
            )

        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "agent_name": row[1],
                "channel_id": row[2],
                "channel_name": row[3],
                "thread_id": row[4],
            }
            for row in rows
        ]

    async def get_messages_with_blocks(self, conversation_id: int | str) -> list[dict]:
        async with self._db.execute(
            "SELECT id, user_id, user_name, user_handle, created_at "
            "FROM messages WHERE conversation_id=? ORDER BY id",
            (int(conversation_id),),
        ) as cursor:
            msg_rows = await cursor.fetchall()

        result = []
        for mr in msg_rows:
            msg_id = mr[0]
            async with self._db.execute(
                "SELECT id, block_type, content, is_user, source_file_id, "
                "tool_block_id, filename, mimetype, size_bytes, "
                "tool_call_id, tool_name, tool_input, tool_output, is_error, "
                "created_at FROM blocks WHERE message_id=? ORDER BY id",
                (msg_id,),
            ) as cursor:
                block_rows = await cursor.fetchall()

            blocks = []
            for br in block_rows:
                block = {
                    "id": br[0],
                    "block_type": br[1],
                    "content": json.loads(br[2]),
                    "created_at": br[14],
                }
                if br[4] is not None:
                    block["source_file_id"] = br[4]
                if br[7] is not None:
                    block["filename"] = br[7]
                if br[9] is not None:
                    block["size_bytes"] = br[9]
                blocks.append(block)

            result.append(
                {
                    "id": msg_id,
                    "user_id": mr[1],
                    "user_name": mr[2],
                    "user_handle": mr[3],
                    "created_at": mr[4],
                    "blocks": blocks,
                }
            )
        return result
