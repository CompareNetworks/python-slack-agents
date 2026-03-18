"""Access provider that allows all users."""

from slack_agents import UserConversationContext
from slack_agents.access.base import AccessGranted, BaseAccessProvider


class Provider(BaseAccessProvider):
    async def check_access(self, *, context: UserConversationContext) -> AccessGranted:
        return AccessGranted()
