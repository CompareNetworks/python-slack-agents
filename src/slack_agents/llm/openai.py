"""OpenAI LLM provider."""

import json
import logging
from typing import AsyncIterator

import openai

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
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1-nano": (0.1, 0.4),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
}


def _convert_messages(messages: list[Message], system_prompt: str = "") -> list[dict]:
    """Convert internal (Anthropic-style) Message format to OpenAI API format."""
    result = []
    if system_prompt:
        result.append({"role": "system", "content": system_prompt})

    for msg in messages:
        content = msg.content

        if isinstance(content, str):
            result.append({"role": msg.role, "content": content})
            continue

        if not isinstance(content, list):
            result.append({"role": msg.role, "content": content})
            continue

        if msg.role == "assistant":
            text_parts = []
            tool_calls = []
            for block in content:
                if block.get("type") == "text":
                    text_parts.append(block["text"])
                elif block.get("type") == "tool_use":
                    tool_calls.append(
                        {
                            "id": block["id"],
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        }
                    )
            msg_dict: dict = {
                "role": "assistant",
                "content": "\n".join(text_parts) if text_parts else None,
            }
            if tool_calls:
                msg_dict["tool_calls"] = tool_calls
            result.append(msg_dict)

        elif msg.role == "user":
            tool_results = [b for b in content if b.get("type") == "tool_result"]
            if tool_results:
                for tr in tool_results:
                    result.append(
                        {
                            "role": "tool",
                            "tool_call_id": tr["tool_use_id"],
                            "content": tr.get("content", ""),
                        }
                    )
            else:
                result.append({"role": msg.role, "content": content})
        else:
            result.append({"role": msg.role, "content": content})

    return result


def _convert_tools(tools: list[dict]) -> list[dict]:
    """Convert Anthropic-style tool definitions to OpenAI format."""
    result = []
    for tool in tools:
        result.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            }
        )
    return result


class Provider(BaseLLMProvider):
    def __init__(
        self,
        model: str,
        api_key: str,
        max_tokens: int,
        max_input_tokens: int,
        base_url: str | None = None,
        input_cost_per_million: float | None = None,
        output_cost_per_million: float | None = None,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.max_input_tokens = max_input_tokens
        self._custom_costs: tuple[float, float] | None = None
        if input_cost_per_million is not None and output_cost_per_million is not None:
            self._custom_costs = (input_cost_per_million, output_cost_per_million)
        self.client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

    def estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
    ) -> float | None:
        costs = self._custom_costs or _MODEL_COSTS.get(self.model)
        if not costs:
            return None
        input_cost, output_cost = costs
        # OpenAI: cached reads at 0.5x, no write penalty
        return (
            input_tokens * input_cost
            + cache_creation_input_tokens * input_cost * 1.0
            + cache_read_input_tokens * input_cost * 0.5
            + output_tokens * output_cost
        ) / 1_000_000

    @observe(name="openai_complete", as_type="generation")
    async def complete(
        self,
        messages: list[Message],
        system_prompt: str = "",
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": _convert_messages(messages, system_prompt),
        }
        if tools:
            kwargs["tools"] = _convert_tools(tools)

        response = await self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message

        text = message.content or ""
        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        input=json.loads(tc.function.arguments),
                    )
                )

        cached = 0
        prompt_tokens = response.usage.prompt_tokens if response.usage else 0
        if response.usage and response.usage.prompt_tokens_details:
            cached = response.usage.prompt_tokens_details.cached_tokens or 0

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason or "",
            input_tokens=prompt_tokens - cached,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            cache_read_input_tokens=cached,
        )

    @observe(name="openai_stream", as_type="generation")
    async def stream(
        self,
        messages: list[Message],
        system_prompt: str = "",
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": _convert_messages(messages, system_prompt),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = _convert_tools(tools)

        pending_tools: dict[int, dict] = {}
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0

        response = await self.client.chat.completions.create(**kwargs)
        async for chunk in response:
            if not chunk.choices:
                if chunk.usage:
                    output_tokens = chunk.usage.completion_tokens
                    details = chunk.usage.prompt_tokens_details
                    cache_read_tokens = (details.cached_tokens or 0) if details else 0
                    input_tokens = chunk.usage.prompt_tokens - cache_read_tokens
                    yield StreamEvent(
                        type="message_end",
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_read_input_tokens=cache_read_tokens,
                    )
                continue

            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

            if delta.content:
                yield StreamEvent(type="text_delta", text=delta.content)

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in pending_tools:
                        pending_tools[idx] = {
                            "id": tc_delta.id or "",
                            "name": (
                                tc_delta.function.name
                                if tc_delta.function and tc_delta.function.name
                                else ""
                            ),
                            "arguments": "",
                        }
                        yield StreamEvent(
                            type="tool_use_start",
                            tool_call=ToolCall(
                                id=pending_tools[idx]["id"],
                                name=pending_tools[idx]["name"],
                                input={},
                            ),
                        )
                    if tc_delta.function and tc_delta.function.arguments:
                        pending_tools[idx]["arguments"] += tc_delta.function.arguments
                        yield StreamEvent(
                            type="tool_use_delta",
                            tool_input_delta=tc_delta.function.arguments,
                        )

            if finish_reason:
                for _idx, pt in pending_tools.items():
                    args = json.loads(pt["arguments"]) if pt["arguments"] else {}
                    yield StreamEvent(
                        type="tool_use_end",
                        tool_call=ToolCall(id=pt["id"], name=pt["name"], input=args),
                    )
                pending_tools.clear()
                yield StreamEvent(type="message_end", stop_reason=finish_reason)

        total_input = input_tokens + cache_read_tokens
        set_span_attrs(
            model=self.model,
            input_tokens=total_input,
            output_tokens=output_tokens,
            usage={
                "input": total_input,
                "output": output_tokens,
                "cache_read_input": cache_read_tokens,
            },
        )
