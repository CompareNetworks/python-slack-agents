"""Access provider that checks user against an allow list of user IDs."""

from slack_agents import UserConversationContext
from slack_agents.access.base import (
    AccessDenied,
    AccessGranted,
    BaseAccessProvider,
)


class Provider(BaseAccessProvider):
    def __init__(self, *, userid_list: list[str], deny_message: str) -> None:
        self._userid_list = set(userid_list)
        self._deny_message = deny_message

    async def check_access(self, *, context: UserConversationContext) -> AccessGranted:
        if context["user_id"] in self._userid_list:
            return AccessGranted()
        raise AccessDenied(self._deny_message)
