"""Abstract base classes for tool providers."""

import re
from abc import ABC, abstractmethod
from typing import Literal, TypedDict

from slack_agents import UserConversationContext
from slack_agents.storage.base import BaseStorageProvider

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ToolException(Exception):
    """Base exception for all tool errors."""


class FileImportToolException(ToolException):
    """Raised when a file import handler fails to process a file."""


# ---------------------------------------------------------------------------
# Typed returns
# ---------------------------------------------------------------------------


class OutputFile(TypedDict):
    """A single file produced by a tool (mirrors InputFile)."""

    data: bytes
    filename: str
    mimeType: str


class ToolResult(TypedDict):
    """Return type for LLM-facing tools."""

    content: str
    is_error: bool
    files: list[OutputFile]


class TextBlock(TypedDict):
    """Anthropic API text content block."""

    type: Literal["text"]
    text: str


class ImageSource(TypedDict):
    """Anthropic API image source."""

    type: Literal["base64"]
    media_type: str
    data: str


class ImageBlock(TypedDict):
    """Anthropic API image content block."""

    type: Literal["image"]
    source: ImageSource


ContentBlock = TextBlock | ImageBlock


class BaseProvider(ABC):
    """Base class for all provider types (tool providers and file importers).

    Subclasses implement _get_all_tools() and call_tool(). The base class
    handles allowed_functions filtering via regex patterns.
    """

    def __init__(self, allowed_functions: list[str], **kwargs):
        self._allowed_patterns = [re.compile(p) for p in allowed_functions]

    @abstractmethod
    def _get_all_tools(self) -> list[dict]:
        """Return all tool definitions. Each dict must have a 'name' key."""

    @property
    def tools(self) -> list[dict]:
        """Return filtered tools matching allowed_functions patterns."""
        return [
            t
            for t in self._get_all_tools()
            if any(p.fullmatch(t["name"]) for p in self._allowed_patterns)
        ]

    @abstractmethod
    async def call_tool(
        self,
        name: str,
        arguments: dict,
        user_conversation_context: UserConversationContext,
        storage: BaseStorageProvider,
    ) -> ToolResult | ContentBlock:
        """Execute a tool call."""

    async def initialize(self) -> None:
        """Initialize the provider (connect to servers, etc.)."""

    async def close(self) -> None:
        """Clean up resources."""


class BaseToolProvider(BaseProvider):
    """Tool provider visible to the LLM."""

    @abstractmethod
    async def call_tool(
        self,
        name: str,
        arguments: dict,
        user_conversation_context: UserConversationContext,
        storage: BaseStorageProvider,
    ) -> ToolResult:
        """Execute a tool call."""


class BaseFileImporterProvider(BaseProvider):
    """File importer provider — invisible to the LLM.

    The framework calls these when files are attached to messages.
    Tool dicts should include 'mimes' (set[str]) and 'max_size' (int).

    Raises FileImportToolException on processing errors (never returns None).
    """

    @abstractmethod
    async def call_tool(
        self,
        name: str,
        arguments: dict,
        user_conversation_context: UserConversationContext,
        storage: BaseStorageProvider,
    ) -> ContentBlock:
        """Execute a file import. Raises FileImportToolException on failure."""
