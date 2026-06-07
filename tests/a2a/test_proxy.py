import pytest

from slack_agents.a2a.proxy import Provider
from slack_agents.llm.base import Message


def _provider(model=None):
    return Provider(model=model, max_input_tokens=1000)


async def _drain(provider, messages, tools):
    return [e async for e in provider.stream(messages=messages, system_prompt="", tools=tools)]


async def test_turn1_emits_tool_use_for_target():
    p = _provider(model="mya2a")
    msgs = [Message(role="user", content="hello there")]
    tools = [{"name": "mya2a", "description": "", "input_schema": {}}]
    events = await _drain(p, msgs, tools)
    tool_ends = [e for e in events if e.type == "tool_use_end"]
    assert len(tool_ends) == 1
    tc = tool_ends[0].tool_call
    assert tc.name == "mya2a" and tc.input == {"message": "hello there"}
    assert events[-1].type == "message_end" and events[-1].stop_reason == "tool_use"


async def test_turn2_relays_tool_result_atomically():
    p = _provider(model="mya2a")
    msgs = [
        Message(role="user", content="hello"),
        Message(
            role="assistant",
            content=[{"type": "tool_use", "id": "x", "name": "mya2a", "input": {}}],
        ),
        Message(
            role="user",
            content=[{"type": "tool_result", "tool_use_id": "x", "content": "the agent reply"}],
        ),
    ]
    tools = [{"name": "mya2a", "description": "", "input_schema": {}}]
    events = await _drain(p, msgs, tools)
    texts = "".join(e.text for e in events if e.type == "text_delta")
    assert texts == "the agent reply"
    assert events[-1].stop_reason == "end_turn"


async def test_single_tool_used_when_model_unset():
    p = _provider(model=None)
    msgs = [Message(role="user", content="hi")]
    tools = [{"name": "only", "description": "", "input_schema": {}}]
    events = await _drain(p, msgs, tools)
    assert [e for e in events if e.type == "tool_use_end"][0].tool_call.name == "only"


async def test_ambiguous_without_model_raises():
    p = _provider(model=None)
    msgs = [Message(role="user", content="hi")]
    tools = [{"name": "a", "input_schema": {}}, {"name": "b", "input_schema": {}}]
    with pytest.raises(SystemExit):
        await _drain(p, msgs, tools)


def test_relays_async_raw_flag():
    assert _provider().relays_async_raw is True


async def test_turn1_extracts_user_text_from_list_content_with_files():
    # With attachments, content is a list: [user text block, file-extraction block(s)].
    p = _provider(model="mya2a")
    msgs = [
        Message(
            role="user",
            content=[
                {"type": "text", "text": "summarize this"},
                {"type": "text", "text": "[File: x.docx — extracted dump...]"},
            ],
        )
    ]
    tools = [{"name": "mya2a", "description": "", "input_schema": {}}]
    events = await _drain(p, msgs, tools)
    tc = [e for e in events if e.type == "tool_use_end"][0].tool_call
    # sends the user's typed text only — not the extracted file dump
    assert tc.input == {"message": "summarize this"}
