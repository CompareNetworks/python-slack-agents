"""Abstract access-control provider interface."""

from abc import ABC, abstractmethod
from typing import TypedDict

from slack_agents import UserConversationContext


class AccessGranted(TypedDict):
    pass


class AccessDenied(Exception):
    pass


class BaseAccessProvider(ABC):
    @abstractmethod
    async def check_access(self, *, context: UserConversationContext) -> AccessGranted:
        """Check access. Returns AccessGranted on success, raises AccessDenied on denial."""
