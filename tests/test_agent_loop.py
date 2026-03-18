"""Tests for agent loop utilities and streaming behavior."""

from unittest.mock import AsyncMock, MagicMock

from slack_agents.agent_loop import _estimate_input_tokens, run_agent_loop_streaming
from slack_agents.llm.base import Message, StreamEvent, ToolCall


def test_estimate_input_tokens_string_messages():
    messages = [Message(role="user", content="Hello world")]
    tokens = _estimate_input_tokens(messages, system_prompt="Be helpful.", tools=None)
    # "Be helpful." = 11 chars + "Hello world" = 11 chars = 22 chars // 3 = 7
    assert tokens == 22 // 3


def test_estimate_input_tokens_includes_system_prompt():
    system = "x" * 300
    tokens = _estimate_input_tokens([], system_prompt=system, tools=None)
    assert tokens == 100  # 300 // 3


def test_estimate_input_tokens_includes_tools():
    tools = [{"name": "test", "description": "a tool", "input_schema": {}}]
    tokens_with = _estimate_input_tokens([], system_prompt="", tools=tools)
    tokens_without = _estimate_input_tokens([], system_prompt="", tools=None)
    assert tokens_with > tokens_without


def test_estimate_input_tokens_list_content():
    content = [{"type": "tool_result", "tool_use_id": "123", "content": "result data"}]
    messages = [Message(role="user", content=content)]
    tokens = _estimate_input_tokens(messages, system_prompt="", tools=None)
    assert tokens > 0


class TestStreamEvent:
    def test_text_delta_defaults(self):
        event = StreamEvent(type="text_delta", text="hello")
        assert event.type == "text_delta"
        assert event.text == "hello"
        assert event.input_tokens == 0
        assert event.output_tokens == 0

    def test_message_end_with_tokens(self):
        event = StreamEvent(
            type="message_end",
            stop_reason="end_turn",
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=300,
        )
        assert event.stop_reason == "end_turn"
        assert event.cache_creation_input_tokens == 200
        assert event.cache_read_input_tokens == 300


def _make_mock_llm(tool_call: ToolCall | None = None):
    """Create a mock LLM that makes one tool call then returns text."""
    llm = MagicMock()
    llm.max_input_tokens = 100_000

    call_count = 0

    async def stream_fn(**kwargs):
        nonlocal call_count
        call_count += 1
        if tool_call and call_count == 1:
            yield StreamEvent(type="tool_use_start", tool_call=tool_call)
            yield StreamEvent(type="tool_use_end", tool_call=tool_call)
            yield StreamEvent(
                type="message_end", stop_reason="tool_use", input_tokens=10, output_tokens=5
            )
        else:
            yield StreamEvent(type="text_delta", text="Done.")
            yield StreamEvent(
                type="message_end", stop_reason="end_turn", input_tokens=10, output_tokens=5
            )

    llm.stream = stream_fn
    return llm


class _MockProvider:
    """Minimal ToolProvider for tests."""

    def __init__(self, tool_defs: list[dict], handler: AsyncMock):
        self._tools = tool_defs
        self._handler = handler

    @property
    def tools(self) -> list[dict]:
        return self._tools

    async def call_tool(self, name, arguments, user_conversation_context=None, storage=None):
        return await self._handler(name, arguments)


class TestToolProviderDispatch:
    async def test_provider_called(self):
        """Tool provider is called when tool name matches."""
        handler = AsyncMock(return_value={"content": "native result", "is_error": False})
        provider = _MockProvider(
            [{"name": "my_tool", "description": "test", "input_schema": {}}], handler
        )
        tc = ToolCall(id="tc1", name="my_tool", input={"x": 1})
        llm = _make_mock_llm(tc)

        events = []
        async for event in run_agent_loop_streaming(
            llm=llm,
            messages=[Message(role="user", content="test")],
            tool_providers=[provider],
        ):
            events.append(event)

        handler.assert_called_once_with("my_tool", {"x": 1})
        tool_done = [e for e in events if isinstance(e, dict) and e.get("status") == "done"]
        assert len(tool_done) == 1
        assert tool_done[0]["tool_result"]["content"] == "native result"

    async def test_correct_provider_dispatched(self):
        """Each tool is dispatched to its own provider."""
        mcp_handler = AsyncMock(
            return_value={"content": "mcp result", "is_error": False, "files": []}
        )
        mcp_provider = _MockProvider(
            [{"name": "mcp_tool", "description": "mcp", "input_schema": {}}], mcp_handler
        )

        native_handler = AsyncMock(return_value={"content": "native result", "is_error": False})
        native_provider = _MockProvider(
            [{"name": "native_only", "description": "test", "input_schema": {}}], native_handler
        )

        tc = ToolCall(id="tc1", name="mcp_tool", input={"q": "test"})
        llm = _make_mock_llm(tc)

        events = []
        async for event in run_agent_loop_streaming(
            llm=llm,
            messages=[Message(role="user", content="test")],
            tool_providers=[mcp_provider, native_provider],
        ):
            events.append(event)

        mcp_handler.assert_called_once_with("mcp_tool", {"q": "test"})
        native_handler.assert_not_called()
