"""Abstract base classes for tool providers."""

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Literal, TypedDict

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


# ---------------------------------------------------------------------------
# Tool errors — schema for the JSON payload in `ToolResult.content` when
# `is_error=True`. Every built-in tool produces errors in this shape so the
# LLM consuming the result can reason about them uniformly.
# ---------------------------------------------------------------------------

# Top-level error types.
ERROR_PERMISSION_DENIED = "permission_denied"  # auth/scope/role refusal — user-level
ERROR_SYSTEM_ERROR = "system_error"  # operational/library/transient
ERROR_AUTH_SETUP_FAILED = "auth_setup_failed"  # auth flow itself broke
ERROR_INPUT_ERROR = "input_error"  # bad call / unknown tool / bad args

# Recovery actions — what the LLM/user should do next.
RECOVERY_RETRY = "retry"  # transient or user-recoverable; just try again
RECOVERY_CONTACT_ADMIN = "contact_admin"  # requires realm/IdP/account admin
RECOVERY_CONTACT_SUPPORT = "contact_support"  # framework operator/dev needs to look at logs
RECOVERY_ABORT = "abort"  # nothing to do for THIS call (LLM may try a different tool)


def make_tool_error(
    *,
    error: str,
    message: str,
    recovery: str,
    code: str | None = None,
    tool: str | None = None,
    server: str | None = None,
    details: dict[str, Any] | None = None,
) -> "ToolResult":
    """Build a `ToolResult` with `is_error=True` and a JSON-encoded error
    payload in `content`.

    The schema (read by the LLM consuming the tool result):

    ```
    {
      "error":    str,              # required, e.g. ERROR_SYSTEM_ERROR
      "code":     str  | optional,  # subtype, e.g. "scope_not_granted"
      "tool":     str  | optional,  # tool name when relevant
      "server":   str  | optional,  # server / provider identifier
      "message":  str,              # required, human-readable
      "recovery": str,              # required, one of RECOVERY_*
      "details":  dict | optional,  # free-form, type-specific
    }
    ```

    Use the module-level constants `ERROR_*` and `RECOVERY_*` to avoid typos.
    `details` is intentionally schema-less — each error type can carry whatever
    structured fields the LLM benefits from seeing (missing scopes, exception
    types, timestamps for support correlation, etc.).
    """
    payload: dict[str, Any] = {"error": error, "message": message, "recovery": recovery}
    if code is not None:
        payload["code"] = code
    if tool is not None:
        payload["tool"] = tool
    if server is not None:
        payload["server"] = server
    if details:
        payload["details"] = details
    return {
        "content": json.dumps(payload, ensure_ascii=False),
        "is_error": True,
        "files": [],
    }


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
