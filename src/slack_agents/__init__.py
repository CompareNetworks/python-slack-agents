"""slack-agents: A Python framework for deploying AI agents as Slack bots."""

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
