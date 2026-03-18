"""Tests for OpenAI message/tool conversion functions."""

from slack_agents.llm.base import Message
from slack_agents.llm.openai import _convert_messages, _convert_tools


class TestConvertMessages:
    def test_simple_user_message(self):
        msgs = [Message(role="user", content="Hello")]
        result = _convert_messages(msgs)
        assert result == [{"role": "user", "content": "Hello"}]

    def test_system_prompt(self):
        result = _convert_messages([], system_prompt="Be helpful.")
        assert result == [{"role": "system", "content": "Be helpful."}]

    def test_assistant_with_tool_use(self):
        msgs = [
            Message(
                role="assistant",
                content=[
                    {"type": "text", "text": "Let me check."},
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "get_weather",
                        "input": {"location": "Paris"},
                    },
                ],
            )
        ]
        result = _convert_messages(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Let me check."
        assert len(result[0]["tool_calls"]) == 1
        tc = result[0]["tool_calls"][0]
        assert tc["id"] == "call_1"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "get_weather"

    def test_user_with_tool_results(self):
        msgs = [
            Message(
                role="user",
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_1",
                        "content": "72°F and sunny",
                    }
                ],
            )
        ]
        result = _convert_messages(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "call_1"
        assert result[0]["content"] == "72°F and sunny"

    def test_multiple_tool_results(self):
        msgs = [
            Message(
                role="user",
                content=[
                    {"type": "tool_result", "tool_use_id": "c1", "content": "result1"},
                    {"type": "tool_result", "tool_use_id": "c2", "content": "result2"},
                ],
            )
        ]
        result = _convert_messages(msgs)
        assert len(result) == 2
        assert result[0]["tool_call_id"] == "c1"
        assert result[1]["tool_call_id"] == "c2"

    def test_full_conversation_round_trip(self):
        msgs = [
            Message(role="user", content="What's the weather?"),
            Message(
                role="assistant",
                content=[
                    {"type": "text", "text": "Checking..."},
                    {"type": "tool_use", "id": "c1", "name": "weather", "input": {}},
                ],
            ),
            Message(
                role="user",
                content=[
                    {"type": "tool_result", "tool_use_id": "c1", "content": "Sunny"},
                ],
            ),
            Message(role="assistant", content=[{"type": "text", "text": "It's sunny!"}]),
        ]
        result = _convert_messages(msgs, system_prompt="System")
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "assistant"
        assert result[3]["role"] == "tool"
        assert result[4]["role"] == "assistant"


class TestConvertTools:
    def test_basic_tool(self):
        tools = [
            {
                "name": "get_weather",
                "description": "Get weather",
                "input_schema": {
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                },
            }
        ]
        result = _convert_tools(tools)
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "get_weather"
        assert result[0]["function"]["parameters"]["type"] == "object"

    def test_missing_description(self):
        tools = [{"name": "tool", "input_schema": {}}]
        result = _convert_tools(tools)
        assert result[0]["function"]["description"] == ""

    def test_multiple_tools(self):
        tools = [
            {"name": "a", "description": "A", "input_schema": {}},
            {"name": "b", "description": "B", "input_schema": {}},
        ]
        result = _convert_tools(tools)
        assert len(result) == 2
        assert result[0]["function"]["name"] == "a"
        assert result[1]["function"]["name"] == "b"
