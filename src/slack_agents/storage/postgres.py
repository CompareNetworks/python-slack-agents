"""PostgreSQL storage provider using asyncpg."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

from slack_agents.storage.base import BaseStorageProvider

logger = logging.getLogger(__name__)

SCHEMA_SQL = (Path(__file__).parent / "postgres.sql").read_text()
SCHEMA_VERSION = "1.0.0"


class Provider(BaseStorageProvider):
    """PostgreSQL-backed storage using asyncpg connection pool."""

    def __init__(self, url: str):
        self._url = url
        self._pool: asyncpg.Pool | None = None

    async def initialize(self) -> None:
        self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=5)
        async with self._pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)
        logger.info("PostgreSQL storage initialized")

    @property
    def pool(self) -> asyncpg.Pool:
        """Access the underlying connection pool."""
        if self._pool is None:
            raise RuntimeError("Storage not initialized — call initialize() first")
        return self._pool

    @property
    def supports_export(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Abstract primitives (kv_store / list_store)
    # ------------------------------------------------------------------

    async def get(self, namespace: str, key: str) -> dict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM kv_store WHERE namespace=$1 AND key=$2",
                namespace,
                key,
            )
            if row:
                return json.loads(row["value"])
            return None

    async def set(self, namespace: str, key: str, value: dict) -> None:
        value_json = json.dumps(value)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO kv_store (namespace, key, value) VALUES ($1, $2, $3::jsonb) "
                "ON CONFLICT (namespace, key) DO UPDATE SET value=$3::jsonb",
                namespace,
                key,
                value_json,
            )

    async def delete(self, namespace: str, key: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM kv_store WHERE namespace=$1 AND key=$2",
                namespace,
                key,
            )

    async def append(self, namespace: str, key: str, item: dict) -> str:
        item_json = json.dumps(item)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO list_store (namespace, key, value) "
                "VALUES ($1, $2, $3::jsonb) RETURNING id",
                namespace,
                key,
                item_json,
            )
            return str(row["id"])

    async def get_list(self, namespace: str, key: str) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, value FROM list_store WHERE namespace=$1 AND key=$2 ORDER BY id",
                namespace,
                key,
            )
            result = []
            for row in rows:
                val = json.loads(row["value"])
                val["id"] = str(row["id"])
                result.append(val)
            return result

    async def query(self, namespace: str, filters: dict) -> list[dict]:
        # Build JSONB containment query
        filter_json = json.dumps(filters)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, value FROM list_store "
                "WHERE namespace=$1 AND value @> $2::jsonb ORDER BY id",
                namespace,
                filter_json,
            )
            result = []
            for row in rows:
                val = json.loads(row["value"])
                val["id"] = str(row["id"])
                result.append(val)
            return result

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

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
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM conversations "
                "WHERE agent_name=$1 AND channel_id=$2 AND thread_id=$3",
                agent_name,
                channel_id,
                thread_id,
            )
            if row:
                if channel_name:
                    await conn.execute(
                        "UPDATE conversations SET channel_name=$1 WHERE id=$2",
                        channel_name,
                        row["id"],
                    )
                return row["id"]
            row = await conn.fetchrow(
                "INSERT INTO conversations "
                "(agent_name, channel_id, channel_name, thread_id, schema_version) "
                "VALUES ($1, $2, $3, $4, $5) "
                "ON CONFLICT (agent_name, channel_id, thread_id) "
                "DO UPDATE SET channel_name=EXCLUDED.channel_name "
                "RETURNING id",
                agent_name,
                channel_id,
                channel_name,
                thread_id,
                SCHEMA_VERSION,
            )
            return row["id"]

    async def has_conversation(self, agent_name: str, channel_id: str, thread_id: str) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM conversations "
                "WHERE agent_name=$1 AND channel_id=$2 AND thread_id=$3",
                agent_name,
                channel_id,
                thread_id,
            )
            return row is not None

    async def create_message(
        self,
        conversation_id: int | str,
        user_id: str,
        user_name: str,
        user_handle: str,
    ) -> int | str:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO messages (conversation_id, user_id, user_name, user_handle, "
                "schema_version) VALUES ($1, $2, $3, $4, $5) RETURNING id",
                conversation_id,
                user_id,
                user_name,
                user_handle,
                SCHEMA_VERSION,
            )
            return row["id"]

    async def get_message_blocks(
        self, conversation_id: int | str
    ) -> list[tuple[int | str, list[dict]]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT m.id AS message_id, 'text' AS block_table, "
                "  tb.is_user, tb.text, "
                "  NULL AS filename, NULL AS mimetype, NULL AS size_bytes, "
                "  NULL::jsonb AS content, NULL AS tool_call_id, "
                "  NULL AS tool_name, NULL::jsonb AS tool_input, NULL AS tool_output, "
                "  NULL::boolean AS is_error, "
                "  tb.created_at "
                "FROM messages m "
                "JOIN text_blocks tb ON tb.message_id = m.id "
                "WHERE m.conversation_id = $1 "
                "UNION ALL "
                "SELECT m.id AS message_id, 'file' AS block_table, "
                "  fb.is_user, NULL AS text, "
                "  fb.filename, fb.mimetype, fb.size_bytes, "
                "  fb.content, NULL AS tool_call_id, "
                "  NULL AS tool_name, NULL::jsonb AS tool_input, NULL AS tool_output, "
                "  NULL::boolean AS is_error, "
                "  fb.created_at "
                "FROM messages m "
                "JOIN file_blocks fb ON fb.message_id = m.id "
                "WHERE m.conversation_id = $1 "
                "  AND (fb.is_user = false OR fb.content->>'type' = 'image') "
                "UNION ALL "
                "SELECT m.id AS message_id, 'tool' AS block_table, "
                "  false AS is_user, NULL AS text, "
                "  NULL AS filename, NULL AS mimetype, NULL AS size_bytes, "
                "  NULL::jsonb AS content, tb.tool_call_id, "
                "  tb.tool_name, tb.tool_input, tb.tool_output, "
                "  tb.is_error, "
                "  tb.created_at "
                "FROM messages m "
                "JOIN tool_blocks tb ON tb.message_id = m.id "
                "WHERE m.conversation_id = $1 "
                "ORDER BY message_id, created_at",
                conversation_id,
            )

        messages_blocks: dict[int, list[dict]] = {}
        for row in rows:
            mid = row["message_id"]
            if mid not in messages_blocks:
                messages_blocks[mid] = []
            block_table = row["block_table"]
            if block_table == "text":
                messages_blocks[mid].append(
                    {
                        "block_type": "user_text" if row["is_user"] else "text",
                        "content": {"text": row["text"]},
                    }
                )
            elif block_table == "file":
                messages_blocks[mid].append(
                    {
                        "block_type": "user_file" if row["is_user"] else "file",
                        "content": json.loads(row["content"]),
                    }
                )
            elif block_table == "tool":
                messages_blocks[mid].append(
                    {
                        "block_type": "tool_use",
                        "content": {
                            "tool_call_id": row["tool_call_id"],
                            "tool_name": row["tool_name"],
                            "tool_input": (
                                json.loads(row["tool_input"]) if row["tool_input"] else {}
                            ),
                            "tool_output": row["tool_output"] or "",
                            "is_error": row["is_error"] or False,
                        },
                    }
                )

        # Return as ordered (message_id, blocks) pairs
        result: list[tuple[int | str, list[dict]]] = []
        for mid in sorted(messages_blocks):
            result.append((mid, messages_blocks[mid]))
        return result

    async def append_text_block(
        self,
        message_id: int | str,
        text: str,
        *,
        is_user: bool = False,
        source_file_id: int | str | None = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO text_blocks "
                "(message_id, is_user, text, source_file_id, schema_version) "
                "VALUES ($1, $2, $3, $4, $5)",
                message_id,
                is_user,
                text,
                source_file_id,
                SCHEMA_VERSION,
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
        content_json = json.dumps(content)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO file_blocks "
                "(message_id, is_user, filename, mimetype, size_bytes, content, "
                "tool_block_id, schema_version) "
                "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8) RETURNING id",
                message_id,
                is_user,
                filename,
                mimetype,
                size_bytes,
                content_json,
                tool_block_id,
                SCHEMA_VERSION,
            )
            return row["id"]

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
        input_json = json.dumps(tool_input)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO tool_blocks "
                "(message_id, tool_call_id, tool_name, tool_input, tool_output, "
                "is_error, schema_version) "
                "VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7) RETURNING id",
                message_id,
                tool_call_id,
                tool_name,
                input_json,
                tool_output,
                is_error,
                SCHEMA_VERSION,
            )
            return row["id"]

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
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO usage_blocks "
                "(message_id, model, version, input_tokens, output_tokens, "
                "cache_creation_input_tokens, cache_read_input_tokens, "
                "peak_single_call_input_tokens, estimated_cost_usd, schema_version) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                message_id,
                model,
                version,
                input_tokens,
                output_tokens,
                cache_creation_input_tokens,
                cache_read_input_tokens,
                peak_single_call_input_tokens,
                estimated_cost_usd,
                SCHEMA_VERSION,
            )

    async def get_tool_call(self, tool_call_id: str) -> dict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT tool_name, tool_input, tool_output, is_error "
                "FROM tool_blocks WHERE tool_call_id = $1",
                tool_call_id,
            )
        if not row:
            return None
        return {
            "tool_name": row["tool_name"],
            "input_json": json.dumps(
                json.loads(row["tool_input"]) if row["tool_input"] else {},
                indent=2,
            ),
            "output_json": row["tool_output"] or "",
            "is_error": row["is_error"],
        }

    async def upsert_heartbeat(self, agent_name: str, last_ping_pong_time: float) -> None:
        ping_at = datetime.fromtimestamp(last_ping_pong_time, tz=timezone.utc)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO agent_heartbeats (agent_name, last_socket_ping_at, schema_version) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (agent_name) DO UPDATE SET last_socket_ping_at = $2",
                agent_name,
                ping_at,
                SCHEMA_VERSION,
            )

    async def get_heartbeat(self, agent_name: str) -> dict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT last_socket_ping_at FROM agent_heartbeats WHERE agent_name = $1",
                agent_name,
            )
        if not row:
            return None
        ping_at = row["last_socket_ping_at"]
        return {"last_ping_pong_time": ping_at.timestamp()}

    async def get_conversations_for_export(
        self,
        agent_name: str,
        *,
        handle: str | None = None,
        date_from: str | datetime | None = None,
        date_to: str | datetime | None = None,
    ) -> list[dict]:
        if isinstance(date_from, str):
            date_from = datetime.fromisoformat(date_from)
        if isinstance(date_to, str):
            date_to = datetime.fromisoformat(date_to)

        conditions = ["c.agent_name = $1"]
        params: list = [agent_name]
        idx = 2

        needs_messages_join = handle is not None or date_from is not None or date_to is not None

        if handle is not None:
            conditions.append(f"m.user_handle = ${idx}")
            params.append(handle)
            idx += 1
        if date_from is not None:
            conditions.append(f"m.created_at >= ${idx}")
            params.append(date_from)
            idx += 1
        if date_to is not None:
            conditions.append(f"m.created_at <= ${idx}")
            params.append(date_to)
            idx += 1

        where = " AND ".join(conditions)
        if needs_messages_join:
            query = (
                f"SELECT DISTINCT c.id, c.agent_name, c.channel_id, "
                f"c.channel_name, c.thread_id "
                f"FROM conversations c "
                f"JOIN messages m ON m.conversation_id = c.id "
                f"WHERE {where} "
                f"ORDER BY c.id"
            )
        else:
            query = (
                f"SELECT c.id, c.agent_name, c.channel_id, "
                f"c.channel_name, c.thread_id "
                f"FROM conversations c "
                f"WHERE {where} "
                f"ORDER BY c.id"
            )

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [dict(row) for row in rows]

    async def get_messages_with_blocks(self, conversation_id: int | str) -> list[dict]:
        async with self._pool.acquire() as conn:
            msg_rows = await conn.fetch(
                "SELECT id, user_id, user_name, user_handle, created_at "
                "FROM messages WHERE conversation_id = $1 ORDER BY id",
                conversation_id,
            )
            text_rows = await conn.fetch(
                "SELECT tb.id, tb.message_id, tb.is_user, tb.text, "
                "  tb.source_file_id, tb.created_at "
                "FROM text_blocks tb "
                "JOIN messages m ON tb.message_id = m.id "
                "WHERE m.conversation_id = $1 ORDER BY tb.id",
                conversation_id,
            )
            file_rows = await conn.fetch(
                "SELECT fb.id, fb.message_id, fb.is_user, fb.filename, fb.mimetype, "
                "  fb.size_bytes, fb.content, fb.tool_block_id, fb.created_at "
                "FROM file_blocks fb "
                "JOIN messages m ON fb.message_id = m.id "
                "WHERE m.conversation_id = $1 ORDER BY fb.id",
                conversation_id,
            )
            tool_rows = await conn.fetch(
                "SELECT tb.id, tb.message_id, tb.tool_call_id, tb.tool_name, "
                "  tb.tool_input, tb.tool_output, tb.is_error, tb.created_at "
                "FROM tool_blocks tb "
                "JOIN messages m ON tb.message_id = m.id "
                "WHERE m.conversation_id = $1 ORDER BY tb.id",
                conversation_id,
            )
            usage_rows = await conn.fetch(
                "SELECT ub.id, ub.message_id, ub.model, ub.version, "
                "  ub.input_tokens, ub.output_tokens, "
                "  ub.cache_creation_input_tokens, ub.cache_read_input_tokens, "
                "  ub.peak_single_call_input_tokens, ub.estimated_cost_usd, ub.created_at "
                "FROM usage_blocks ub "
                "JOIN messages m ON ub.message_id = m.id "
                "WHERE m.conversation_id = $1 ORDER BY ub.id",
                conversation_id,
            )

        blocks_by_msg: dict[int, list[dict]] = {}
        for row in text_rows:
            mid = row["message_id"]
            block = {
                "id": row["id"],
                "block_type": "user_text" if row["is_user"] else "text",
                "content": {"text": row["text"]},
                "source_file_id": row["source_file_id"],
                "created_at": row["created_at"],
            }
            blocks_by_msg.setdefault(mid, []).append(block)
        for row in file_rows:
            mid = row["message_id"]
            block_type = "user_file" if row["is_user"] else "file"
            content = json.loads(row["content"])
            if not row["is_user"] and "filename" not in content:
                content["filename"] = row["filename"]
            if not row["is_user"] and "mimeType" not in content:
                content["mimeType"] = row["mimetype"] or "application/octet-stream"
            blocks_by_msg.setdefault(mid, []).append(
                {
                    "id": row["id"],
                    "block_type": block_type,
                    "content": content,
                    "filename": row["filename"],
                    "size_bytes": row["size_bytes"],
                    "created_at": row["created_at"],
                }
            )
        for row in tool_rows:
            mid = row["message_id"]
            blocks_by_msg.setdefault(mid, []).append(
                {
                    "id": row["id"],
                    "block_type": "tool_use",
                    "content": {
                        "tool_call_id": row["tool_call_id"],
                        "tool_name": row["tool_name"],
                        "tool_input": (json.loads(row["tool_input"]) if row["tool_input"] else {}),
                        "tool_output": row["tool_output"] or "",
                        "is_error": row["is_error"],
                    },
                    "created_at": row["created_at"],
                }
            )
        for row in usage_rows:
            mid = row["message_id"]
            blocks_by_msg.setdefault(mid, []).append(
                {
                    "id": row["id"],
                    "block_type": "usage",
                    "content": {
                        "model": row["model"],
                        "version": row["version"],
                        "input_tokens": row["input_tokens"],
                        "output_tokens": row["output_tokens"],
                        "cache_creation_input_tokens": row["cache_creation_input_tokens"],
                        "cache_read_input_tokens": row["cache_read_input_tokens"],
                        "peak_single_call_input_tokens": row["peak_single_call_input_tokens"],
                        "estimated_cost_usd": (
                            float(row["estimated_cost_usd"])
                            if row["estimated_cost_usd"] is not None
                            else None
                        ),
                    },
                    "created_at": row["created_at"],
                }
            )

        for mid in blocks_by_msg:
            blocks_by_msg[mid].sort(key=lambda b: (b["created_at"], b["id"]))

        result = []
        for row in msg_rows:
            result.append(
                {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "user_name": row["user_name"],
                    "user_handle": row["user_handle"],
                    "created_at": row["created_at"],
                    "blocks": blocks_by_msg.get(row["id"], []),
                }
            )
        return result
