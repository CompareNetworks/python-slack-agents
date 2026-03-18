"""Tests for conversation store block reconstruction logic."""

import pytest

from slack_agents.conversations import ConversationManager, _reconstruct_messages
from slack_agents.llm.base import Message
from slack_agents.storage.sqlite import Provider as SqliteProvider


class TestReconstructMessages:
    """Test _reconstruct_messages which converts blocks -> LLM Messages."""

    def test_user_text_only(self):
        blocks = [{"block_type": "user_text", "content": {"text": "hello"}}]
        msgs = _reconstruct_messages(blocks)
        assert len(msgs) == 1
        assert msgs[0] == Message(role="user", content="hello")

    def test_user_text_simplifies_single_text(self):
        """Single user_text block should produce a plain string, not a list."""
        blocks = [{"block_type": "user_text", "content": {"text": "hi"}}]
        msgs = _reconstruct_messages(blocks)
        assert isinstance(msgs[0].content, str)

    def test_user_text_and_file(self):
        blocks = [
            {"block_type": "user_text", "content": {"text": "check this"}},
            {
                "block_type": "user_file",
                "content": {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": "abc123"},
                },
            },
        ]
        msgs = _reconstruct_messages(blocks)
        assert len(msgs) == 1
        assert msgs[0].role == "user"
        assert isinstance(msgs[0].content, list)
        assert len(msgs[0].content) == 2
        assert msgs[0].content[0] == {"type": "text", "text": "check this"}
        assert msgs[0].content[1]["type"] == "image"

    def test_simple_assistant_text(self):
        """user_text + assistant text -> 2 messages."""
        blocks = [
            {"block_type": "user_text", "content": {"text": "hi"}},
            {"block_type": "text", "content": {"text": "Hello! How can I help?"}},
        ]
        msgs = _reconstruct_messages(blocks)
        assert len(msgs) == 2
        assert msgs[0] == Message(role="user", content="hi")
        assert msgs[1] == Message(
            role="assistant", content=[{"type": "text", "text": "Hello! How can I help?"}]
        )

    def test_tool_use_iteration(self):
        """user_text + text + tool_use + text -> full iteration cycle."""
        blocks = [
            {"block_type": "user_text", "content": {"text": "search for X"}},
            {"block_type": "text", "content": {"text": "Let me search."}},
            {
                "block_type": "tool_use",
                "content": {
                    "tool_call_id": "tc1",
                    "tool_name": "search",
                    "tool_input": {"query": "X"},
                    "tool_output": "Found X.",
                    "is_error": False,
                },
            },
            {"block_type": "text", "content": {"text": "I found X for you."}},
        ]
        msgs = _reconstruct_messages(blocks)
        assert len(msgs) == 4

        # User message
        assert msgs[0] == Message(role="user", content="search for X")

        # First assistant: text + tool_use
        assert msgs[1].role == "assistant"
        assert len(msgs[1].content) == 2
        assert msgs[1].content[0] == {"type": "text", "text": "Let me search."}
        assert msgs[1].content[1] == {
            "type": "tool_use",
            "id": "tc1",
            "name": "search",
            "input": {"query": "X"},
        }

        # Tool result
        assert msgs[2].role == "user"
        assert msgs[2].content == [
            {"type": "tool_result", "tool_use_id": "tc1", "content": "Found X."}
        ]

        # Final assistant text
        assert msgs[3].role == "assistant"
        assert msgs[3].content == [{"type": "text", "text": "I found X for you."}]

    def test_multiple_tools_in_one_iteration(self):
        """Multiple tool_use blocks before next text -> single assistant with all tools."""
        blocks = [
            {"block_type": "user_text", "content": {"text": "do stuff"}},
            {"block_type": "text", "content": {"text": "I'll use two tools."}},
            {
                "block_type": "tool_use",
                "content": {
                    "tool_call_id": "tc1",
                    "tool_name": "tool_a",
                    "tool_input": {},
                    "tool_output": "result_a",
                    "is_error": False,
                },
            },
            {
                "block_type": "tool_use",
                "content": {
                    "tool_call_id": "tc2",
                    "tool_name": "tool_b",
                    "tool_input": {},
                    "tool_output": "result_b",
                    "is_error": False,
                },
            },
            {"block_type": "text", "content": {"text": "Done."}},
        ]
        msgs = _reconstruct_messages(blocks)
        assert len(msgs) == 4

        # Assistant with text + 2 tool_use blocks
        assert msgs[1].role == "assistant"
        assert len(msgs[1].content) == 3
        assert msgs[1].content[0]["type"] == "text"
        assert msgs[1].content[1]["type"] == "tool_use"
        assert msgs[1].content[2]["type"] == "tool_use"

        # Tool results for both
        assert msgs[2].role == "user"
        assert len(msgs[2].content) == 2
        assert msgs[2].content[0]["tool_use_id"] == "tc1"
        assert msgs[2].content[1]["tool_use_id"] == "tc2"

    def test_error_tool_sets_is_error(self):
        blocks = [
            {"block_type": "user_text", "content": {"text": "try it"}},
            {
                "block_type": "tool_use",
                "content": {
                    "tool_call_id": "tc1",
                    "tool_name": "broken",
                    "tool_input": {},
                    "tool_output": "boom",
                    "is_error": True,
                },
            },
            {"block_type": "text", "content": {"text": "That failed."}},
        ]
        msgs = _reconstruct_messages(blocks)
        # Tool result should have is_error
        tool_result_msg = msgs[2]
        assert tool_result_msg.content[0].get("is_error") is True

    def test_usage_blocks_ignored(self):
        blocks = [
            {"block_type": "user_text", "content": {"text": "hi"}},
            {"block_type": "text", "content": {"text": "hello"}},
            {
                "block_type": "usage",
                "content": {
                    "model": "claude-sonnet-4-6",
                    "input_tokens": 100,
                    "output_tokens": 50,
                },
            },
        ]
        msgs = _reconstruct_messages(blocks)
        assert len(msgs) == 2  # usage not included

    def test_empty_blocks(self):
        msgs = _reconstruct_messages([])
        assert msgs == []

    def test_no_user_blocks_only_assistant(self):
        """Edge case: only assistant text blocks (no user_text)."""
        blocks = [
            {"block_type": "text", "content": {"text": "I was asked something."}},
        ]
        msgs = _reconstruct_messages(blocks)
        assert len(msgs) == 1
        assert msgs[0].role == "assistant"

    def test_two_iterations_with_tools(self):
        """Two complete tool-use iterations: text+tool+text+tool+text."""
        blocks = [
            {"block_type": "user_text", "content": {"text": "complex task"}},
            {"block_type": "text", "content": {"text": "Step 1."}},
            {
                "block_type": "tool_use",
                "content": {
                    "tool_call_id": "tc1",
                    "tool_name": "step1",
                    "tool_input": {},
                    "tool_output": "ok1",
                    "is_error": False,
                },
            },
            {"block_type": "text", "content": {"text": "Step 2."}},
            {
                "block_type": "tool_use",
                "content": {
                    "tool_call_id": "tc2",
                    "tool_name": "step2",
                    "tool_input": {},
                    "tool_output": "ok2",
                    "is_error": False,
                },
            },
            {"block_type": "text", "content": {"text": "All done."}},
        ]
        msgs = _reconstruct_messages(blocks)
        # user, asst(text+tool), user(result), asst(text+tool), user(result), asst(text)
        assert len(msgs) == 6
        assert msgs[0].role == "user"
        assert msgs[1].role == "assistant"
        assert msgs[2].role == "user"
        assert msgs[3].role == "assistant"
        assert msgs[4].role == "user"
        assert msgs[5].role == "assistant"


class TestSupportsExport:
    @pytest.mark.asyncio
    async def test_sqlite_default_does_not_support_export(self):
        storage = SqliteProvider(path=":memory:")
        await storage.initialize()
        manager = ConversationManager(storage)
        assert manager.supports_export is False
        await storage.close()

    @pytest.mark.asyncio
    async def test_export_raises_for_unsupported_backend(self):
        storage = SqliteProvider(path=":memory:")
        await storage.initialize()
        manager = ConversationManager(storage)
        with pytest.raises(NotImplementedError, match="does not support conversation export"):
            await manager.get_conversations_for_export("my-agent")
        await storage.close()


class TestGetToolCall:
    """Test get_tool_call round-trip with SQLite."""

    @pytest.mark.asyncio
    async def test_get_tool_call_round_trip(self):
        storage = SqliteProvider(path=":memory:")
        await storage.initialize()
        try:
            conv_id = await storage.get_or_create_conversation("test-agent", "C123", "T456")
            msg_id = await storage.create_message(conv_id, "U1", "Alice", "alice")
            await storage.append_tool_block(
                msg_id,
                tool_call_id="toolu_abc123",
                tool_name="web_search",
                tool_input={"query": "hello world"},
                tool_output="Found results.",
                is_error=False,
            )
            result = await storage.get_tool_call("toolu_abc123")
            assert result is not None
            assert result["tool_name"] == "web_search"
            assert '"hello world"' in result["input_json"]
            assert result["output_json"] == "Found results."
            assert result["is_error"] is False
        finally:
            await storage.close()

    @pytest.mark.asyncio
    async def test_get_tool_call_not_found(self):
        storage = SqliteProvider(path=":memory:")
        await storage.initialize()
        try:
            result = await storage.get_tool_call("nonexistent")
            assert result is None
        finally:
            await storage.close()

    @pytest.mark.asyncio
    async def test_get_tool_call_via_manager(self):
        """ConversationManager delegates get_tool_call to storage."""
        storage = SqliteProvider(path=":memory:")
        await storage.initialize()
        try:
            manager = ConversationManager(storage)
            conv_id = await manager.get_or_create_conversation("test-agent", "C123", "T789")
            msg_id = await manager.create_message(conv_id, "U1", "Bob", "bob")
            await manager.append_tool_block(
                msg_id,
                tool_call_id="toolu_xyz",
                tool_name="calculator",
                tool_input={"expression": "2+2"},
                tool_output="4",
                is_error=False,
            )
            result = await manager.get_tool_call("toolu_xyz")
            assert result is not None
            assert result["tool_name"] == "calculator"
        finally:
            await storage.close()


class TestRoundTrip:
    """Test full conversation -> message -> blocks -> get_messages round-trip with SQLite."""

    @pytest.mark.asyncio
    async def test_conversation_round_trip(self):
        storage = SqliteProvider(path=":memory:")
        await storage.initialize()
        try:
            manager = ConversationManager(storage)

            # Create conversation and message
            conv_id = await manager.get_or_create_conversation(
                "test-agent", "C001", "T001", channel_name="general"
            )
            msg_id = await manager.create_message(conv_id, "U1", "Alice", "alice")

            # Add blocks: user text, assistant text, tool use
            await manager.append_text_block(msg_id, "What is 2+2?", is_user=True)
            await manager.append_text_block(msg_id, "Let me calculate that.")
            await manager.append_tool_block(
                msg_id,
                tool_call_id="tc_calc",
                tool_name="calculator",
                tool_input={"expr": "2+2"},
                tool_output="4",
                is_error=False,
            )
            await manager.append_text_block(msg_id, "The answer is 4.")

            # Reconstruct messages
            messages = await manager.get_messages(conv_id)

            # Should produce: user, assistant(text+tool), user(tool_result), assistant(text)
            assert len(messages) == 4
            assert messages[0] == Message(role="user", content="What is 2+2?")
            assert messages[1].role == "assistant"
            assert messages[1].content[0] == {
                "type": "text",
                "text": "Let me calculate that.",
            }
            assert messages[1].content[1]["type"] == "tool_use"
            assert messages[2].role == "user"
            assert messages[2].content[0]["type"] == "tool_result"
            assert messages[3].role == "assistant"
            assert messages[3].content == [{"type": "text", "text": "The answer is 4."}]
        finally:
            await storage.close()

    @pytest.mark.asyncio
    async def test_has_conversation(self):
        storage = SqliteProvider(path=":memory:")
        await storage.initialize()
        try:
            manager = ConversationManager(storage)
            assert not await manager.has_conversation("agent", "C1", "T1")
            await manager.get_or_create_conversation("agent", "C1", "T1")
            assert await manager.has_conversation("agent", "C1", "T1")
        finally:
            await storage.close()

    @pytest.mark.asyncio
    async def test_heartbeat_round_trip(self):
        storage = SqliteProvider(path=":memory:")
        await storage.initialize()
        try:
            manager = ConversationManager(storage)
            await manager.upsert_heartbeat("agent", 1700000000.0)
            hb = await storage.get_heartbeat("agent")
            assert hb is not None
            assert hb["last_ping_pong_time"] == 1700000000.0
        finally:
            await storage.close()
