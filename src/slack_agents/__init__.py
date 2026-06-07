"""slack-agents: A Python framework for deploying AI agents as Slack bots."""

import asyncio
from dataclasses import dataclass, field
from importlib.metadata import version
from typing import NotRequired, TypedDict

__version__ = version("python-slack-agents")


class UserConversationContext(TypedDict):
    """Identity and location of the user making a request."""

    user_id: str
    user_name: str
    user_handle: str
    channel_id: str
    channel_name: str
    thread_id: str


class InputFile(TypedDict):
    """Structured file input for file import handlers."""

    file_bytes: bytes
    mimetype: str
    filename: str
    file_id: NotRequired[str]


@dataclass
class OAuthCallbackResult:
    """Result delivered by the in-process callback server to a waiting OAuth flow."""

    code: str | None = None
    state: str | None = None
    error: str | None = None
    error_description: str | None = None


class PendingFlowsRegistry:
    """In-memory map: SDK state value -> Future to be resolved by the callback handler."""

    def __init__(self) -> None:
        self._flows: dict[str, asyncio.Future[OAuthCallbackResult]] = {}

    def register(self, state: str) -> asyncio.Future[OAuthCallbackResult]:
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[OAuthCallbackResult] = loop.create_future()
        self._flows[state] = fut
        return fut

    def resolve(self, state: str, result: OAuthCallbackResult) -> bool:
        fut = self._flows.pop(state, None)
        if fut is None or fut.done():
            return False
        fut.set_result(result)
        return True

    def discard(self, state: str) -> None:
        self._flows.pop(state, None)


@dataclass
class FrameworkContext:
    """Shared services injected into providers that declare they need them.

    Providers receive this only if their __init__ accepts a `framework_ctx` parameter
    (see `slack_agents.config.load_plugin`).
    """

    bot_token: str
    agent_name: str
    # The next two are typed loosely to avoid importing slack_sdk / storage at module
    # load time; the framework binds the real instances at runtime.
    slack_client: object = None
    storage: object = None
    pending_flows: PendingFlowsRegistry = field(default_factory=PendingFlowsRegistry)
    # Called by async A2A delivery to surface a long-running task's result.
    # Signature: deliver_async_result(channel_id, thread_id, user_id, text, is_error)
    deliver_async_result: object = None
    # Per-turn raw file uploads, keyed by thread_id, populated by SlackAgent._run_turn so
    # tools (e.g. a2a.agent) can forward attachments. Each value is a list of
    # {data: bytes, filename, mimeType}. Cleared after each turn.
    pending_uploads: dict = field(default_factory=dict)
