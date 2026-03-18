"""Abstract LLM provider interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class StreamEvent:
    """A single event from an LLM stream."""

    type: str  # "text_delta", "tool_use_start", "tool_use_delta", "tool_use_end", "message_end"
    text: str = ""
    tool_call: ToolCall | None = None
    # Partial JSON string for tool input accumulation
    tool_input_delta: str = ""
    stop_reason: str = ""
    # Cumulative token counts across all LLM calls in the agent loop.
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    # Peak input tokens sent in any single LLM call (input + cache tokens).
    # Use this — not the cumulative total — to gauge proximity to the context limit.
    peak_single_call_input_tokens: int = 0


@dataclass
class Message:
    role: str  # "user", "assistant", "tool_result"
    content: str | list[dict]


class BaseLLMProvider(ABC):
    max_input_tokens: int
    model: str

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        system_prompt: str = "",
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """Send messages to the LLM and get a complete response."""

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        system_prompt: str = "",
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream responses from the LLM, yielding StreamEvents."""

    @abstractmethod
    def estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
    ) -> float | None:
        """Estimate cost in USD. Returns None if model not in price table."""
