"""Dumb passthrough 'LLM' that routes Option B to a single A2A tool."""

import uuid
from typing import AsyncIterator

from slack_agents.llm.base import BaseLLMProvider, LLMResponse, Message, StreamEvent, ToolCall


class Provider(BaseLLMProvider):
    """Not a real LLM: turn 1 calls the target A2A tool, turn 2 relays its result."""

    relays_async_raw = True  # framework posts async results raw instead of re-entering the loop

    def __init__(self, *, model: str | None = None, max_input_tokens: int = 200000, **_):
        self.model = model or "a2a-proxy"
        self._target = model
        self.max_input_tokens = max_input_tokens

    def _pick_target(self, tools: list[dict] | None) -> str:
        names = [t["name"] for t in (tools or [])]
        if self._target:
            return self._target
        if len(names) == 1:
            return names[0]
        raise SystemExit(
            "a2a.proxy: set `model:` to the target a2a tool name "
            f"(found {len(names)} tools: {names})."
        )

    def _latest_user_text(self, messages: list[Message]) -> str:
        for m in reversed(messages):
            if m.role != "user":
                continue
            if isinstance(m.content, str):
                return m.content
            if isinstance(m.content, list):
                # With file attachments the content is a list of blocks. Skip
                # tool-result messages (handled by _pending_tool_result). The user's
                # typed text is the first text block (file-extraction blocks follow);
                # the raw files are forwarded separately as pending_uploads.
                if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m.content):
                    continue
                texts = [
                    b.get("text", "")
                    for b in m.content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                return texts[0] if texts else ""
        return ""

    def _pending_tool_result(self, messages: list[Message]) -> str | None:
        if not messages:
            return None
        last = messages[-1]
        if last.role == "user" and isinstance(last.content, list):
            texts = [b.get("content", "") for b in last.content if b.get("type") == "tool_result"]
            if texts:
                return "\n".join(t for t in texts if t)
        return None

    async def stream(
        self,
        messages: list[Message],
        system_prompt: str = "",
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        pending = self._pending_tool_result(messages)
        if pending is not None:  # turn 2 — relay atomically
            yield StreamEvent(type="text_delta", text=pending)
            yield StreamEvent(type="message_end", stop_reason="end_turn")
            return
        target = self._pick_target(tools)  # turn 1 — call the tool
        # Tool-use ids must be unique per call — the framework keys persisted
        # tool blocks (and Slack status messages) by id, so a constant id would
        # collide across turns and surface a stale block.
        tc = ToolCall(
            id=f"a2a-proxy-{uuid.uuid4().hex}",
            name=target,
            input={"message": self._latest_user_text(messages)},
        )
        yield StreamEvent(type="tool_use_start", tool_call=tc)
        yield StreamEvent(type="tool_use_end", tool_call=tc)
        yield StreamEvent(type="message_end", stop_reason="tool_use")

    async def complete(
        self,
        messages: list[Message],
        system_prompt: str = "",
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        pending = self._pending_tool_result(messages)
        if pending is not None:
            return LLMResponse(text=pending, stop_reason="end_turn")
        target = self._pick_target(tools)
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    id=f"a2a-proxy-{uuid.uuid4().hex}",
                    name=target,
                    input={"message": self._latest_user_text(messages)},
                )
            ],
            stop_reason="tool_use",
        )

    def estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
    ) -> float | None:
        return None
