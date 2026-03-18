"""Anthropic (Claude) LLM provider."""

import json
import logging
from typing import AsyncIterator

import anthropic

from slack_agents.llm.base import (
    BaseLLMProvider,
    LLMResponse,
    Message,
    StreamEvent,
    ToolCall,
)
from slack_agents.observability import observe, set_span_attrs

logger = logging.getLogger(__name__)

# Cost per token (USD) — (input_cost_per_1M, output_cost_per_1M)
_MODEL_COSTS: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-opus-4-6": (15.0, 75.0),
    "claude-haiku-4-5-20251001": (0.8, 4.0),
}


def _convert_messages(messages: list[Message]) -> list[dict]:
    """Convert internal Message format to Anthropic API format."""
    result = []
    for msg in messages:
        result.append({"role": msg.role, "content": msg.content})
    return result


class Provider(BaseLLMProvider):
    def __init__(self, model: str, api_key: str, max_tokens: int, max_input_tokens: int):
        self.model = model
        self.max_tokens = max_tokens
        self.max_input_tokens = max_input_tokens
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    def estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
    ) -> float | None:
        costs = _MODEL_COSTS.get(self.model)
        if not costs:
            return None
        input_cost, output_cost = costs
        # Anthropic: cached reads at 0.1x, writes at 1.25x
        return (
            input_tokens * input_cost
            + cache_creation_input_tokens * input_cost * 1.25
            + cache_read_input_tokens * input_cost * 0.1
            + output_tokens * output_cost
        ) / 1_000_000

    @observe(name="anthropic_complete", as_type="generation")
    async def complete(
        self,
        messages: list[Message],
        system_prompt: str = "",
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": _convert_messages(messages),
            "cache_control": {"type": "ephemeral"},
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = tools

        response = await self.client.messages.create(**kwargs)

        text = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text = block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_creation_input_tokens=getattr(response.usage, "cache_creation_input_tokens", 0)
            or 0,
            cache_read_input_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        )

    @observe(name="anthropic_stream", as_type="generation")
    async def stream(
        self,
        messages: list[Message],
        system_prompt: str = "",
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": _convert_messages(messages),
            "cache_control": {"type": "ephemeral"},
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = tools

        current_tool: dict | None = None
        had_tool_use = False
        tool_input_parts: list[str] = []
        input_tokens = 0
        output_tokens = 0
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 0

        async with self.client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "message_start":
                    if hasattr(event, "message") and event.message.usage:
                        input_tokens = event.message.usage.input_tokens
                        usage = event.message.usage
                        cache_creation_input_tokens = (
                            getattr(usage, "cache_creation_input_tokens", 0) or 0
                        )
                        cache_read_input_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0

                elif event.type == "content_block_start":
                    if event.content_block.type == "text":
                        if had_tool_use:
                            yield StreamEvent(type="text_delta", text="\n\n")
                    elif event.content_block.type == "tool_use":
                        current_tool = {
                            "id": event.content_block.id,
                            "name": event.content_block.name,
                        }
                        tool_input_parts = []
                        yield StreamEvent(
                            type="tool_use_start",
                            tool_call=ToolCall(
                                id=event.content_block.id,
                                name=event.content_block.name,
                                input={},
                            ),
                        )

                elif event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        yield StreamEvent(type="text_delta", text=event.delta.text)
                    elif event.delta.type == "input_json_delta":
                        tool_input_parts.append(event.delta.partial_json)
                        yield StreamEvent(
                            type="tool_use_delta",
                            tool_input_delta=event.delta.partial_json,
                        )

                elif event.type == "content_block_stop":
                    if current_tool:
                        input_json = "".join(tool_input_parts)
                        tool_input = json.loads(input_json) if input_json else {}
                        yield StreamEvent(
                            type="tool_use_end",
                            tool_call=ToolCall(
                                id=current_tool["id"],
                                name=current_tool["name"],
                                input=tool_input,
                            ),
                        )
                        had_tool_use = True
                        current_tool = None
                        tool_input_parts = []

                elif event.type == "message_delta":
                    if event.usage:
                        output_tokens = event.usage.output_tokens
                    yield StreamEvent(
                        type="message_end",
                        stop_reason=event.delta.stop_reason,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_creation_input_tokens=cache_creation_input_tokens,
                        cache_read_input_tokens=cache_read_input_tokens,
                    )

        total_input = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
        set_span_attrs(
            model=self.model,
            input_tokens=total_input,
            output_tokens=output_tokens,
            usage={
                "input": total_input,
                "output": output_tokens,
                "cache_read_input": cache_read_input_tokens,
                "cache_creation_input": cache_creation_input_tokens,
            },
        )
