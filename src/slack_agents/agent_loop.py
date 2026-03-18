"""Core agent loop: LLM -> tools -> LLM -> ... until done."""

import asyncio
import json
import logging
from typing import AsyncIterator

from slack_agents import UserConversationContext
from slack_agents.llm import CHARS_PER_TOKEN
from slack_agents.llm.base import BaseLLMProvider, LLMResponse, Message, StreamEvent
from slack_agents.observability import observe
from slack_agents.storage.base import BaseStorageProvider
from slack_agents.tools.base import BaseToolProvider, ToolResult

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 15


def _estimate_input_tokens(
    messages: list[Message],
    system_prompt: str,
    tools: list[dict] | None,
) -> int:
    """Estimate total input tokens from character count."""
    chars = len(system_prompt)
    chars += len(json.dumps(tools)) if tools else 0
    for msg in messages:
        if isinstance(msg.content, str):
            chars += len(msg.content)
        else:
            chars += len(json.dumps(msg.content))
    return chars // CHARS_PER_TOKEN


@observe(name="agent_loop_streaming")
async def run_agent_loop_streaming(
    llm: BaseLLMProvider,
    messages: list[Message],
    system_prompt: str = "",
    tool_providers: list[BaseToolProvider] | None = None,
    user_conversation_context: UserConversationContext | None = None,
    storage: BaseStorageProvider | None = None,
) -> AsyncIterator[StreamEvent | dict]:
    """Run the agent loop with streaming.

    Yields StreamEvents for text and status dicts for tool calls.
    """
    providers = tool_providers or []
    tools = [t for p in providers for t in p.tools] or None
    provider_map = {t["name"]: p for p in providers for t in p.tools}
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_creation_input_tokens = 0
    total_cache_read_input_tokens = 0
    peak_single_call_input_tokens = 0

    for iteration in range(MAX_ITERATIONS):
        logger.info("Agent loop streaming iteration %d", iteration + 1)

        estimated = _estimate_input_tokens(messages, system_prompt, tools)
        if estimated > llm.max_input_tokens:
            logger.warning(
                "Estimated input ~%d tokens exceeds limit of %d",
                estimated,
                llm.max_input_tokens,
            )
            yield StreamEvent(
                type="text_delta",
                text="\n\n_This conversation has grown too long. Please start a new thread._",
            )
            yield StreamEvent(
                type="message_end",
                stop_reason="max_input_tokens",
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                cache_creation_input_tokens=total_cache_creation_input_tokens,
                cache_read_input_tokens=total_cache_read_input_tokens,
                peak_single_call_input_tokens=peak_single_call_input_tokens,
            )
            return

        collected_text = ""
        collected_tool_calls = []
        current_tool_inputs: dict[str, list[str]] = {}
        stop_reason = ""

        async for event in llm.stream(
            messages=messages,
            system_prompt=system_prompt,
            tools=tools or None,
        ):
            if event.type == "text_delta":
                collected_text += event.text
                yield event

            elif event.type == "tool_use_start":
                if event.tool_call:
                    current_tool_inputs[event.tool_call.id] = []

            elif event.type == "tool_use_delta":
                for tid in current_tool_inputs:
                    current_tool_inputs[tid].append(event.tool_input_delta)

            elif event.type == "tool_use_end":
                if event.tool_call:
                    collected_tool_calls.append(event.tool_call)

            elif event.type == "message_end":
                stop_reason = event.stop_reason
                total_input_tokens += event.input_tokens
                total_output_tokens += event.output_tokens
                total_cache_creation_input_tokens += event.cache_creation_input_tokens
                total_cache_read_input_tokens += event.cache_read_input_tokens
                call_input = (
                    event.input_tokens
                    + event.cache_creation_input_tokens
                    + event.cache_read_input_tokens
                )
                peak_single_call_input_tokens = max(peak_single_call_input_tokens, call_input)

        if not collected_tool_calls:
            yield StreamEvent(
                type="message_end",
                stop_reason=stop_reason,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                cache_creation_input_tokens=total_cache_creation_input_tokens,
                cache_read_input_tokens=total_cache_read_input_tokens,
                peak_single_call_input_tokens=peak_single_call_input_tokens,
            )
            return

        response = LLMResponse(
            text=collected_text,
            tool_calls=collected_tool_calls,
            stop_reason=stop_reason,
        )
        assistant_content = _build_assistant_content(response)
        messages.append(Message(role="assistant", content=assistant_content))

        for tc in collected_tool_calls:
            yield {
                "type": "tool_status",
                "tool_id": tc.id,
                "tool_name": tc.name,
                "status": "calling",
                "tool_input": tc.input,
            }

        async def _call(tc) -> ToolResult:
            provider = provider_map.get(tc.name)
            if provider:
                return await provider.call_tool(
                    tc.name, tc.input, user_conversation_context, storage
                )
            return {"content": f"Unknown tool: {tc.name}", "is_error": True, "files": []}

        results = await asyncio.gather(*[_call(tc) for tc in collected_tool_calls])

        tool_results = []
        for tc, result in zip(collected_tool_calls, results):
            yield {
                "type": "tool_status",
                "tool_id": tc.id,
                "tool_name": tc.name,
                "status": "done",
                "tool_input": tc.input,
                "tool_result": result,
            }
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result["content"],
                    **({"is_error": True} if result["is_error"] else {}),
                }
            )

        messages.append(Message(role="user", content=tool_results))

    yield StreamEvent(type="text_delta", text="\n\n_Reached maximum tool-calling steps._")
    yield StreamEvent(
        type="message_end",
        stop_reason="max_iterations",
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        cache_creation_input_tokens=total_cache_creation_input_tokens,
        cache_read_input_tokens=total_cache_read_input_tokens,
        peak_single_call_input_tokens=peak_single_call_input_tokens,
    )


def _build_assistant_content(response: LLMResponse) -> list[dict]:
    """Build Anthropic-style assistant content blocks from an LLMResponse."""
    blocks: list[dict] = []
    if response.text:
        blocks.append({"type": "text", "text": response.text})
    for tc in response.tool_calls:
        blocks.append(
            {
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            }
        )
    return blocks
