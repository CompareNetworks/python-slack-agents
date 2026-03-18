# Access Control

Control which Slack users can interact with each agent. The `access` key is required in every agent's `config.yaml`.

## Configuration

### Allow all users

```yaml
access:
  type: slack_agents.access.allow_all
```

### Allow list

Restrict access to specific Slack user IDs:

```yaml
access:
  type: slack_agents.access.allow_list
  userid_list:
    - U1234567890
    - U9876543210
  deny_message: "You don't have access to this agent. Ask in #help-infra to request access."
```

The `deny_message` is shown as an ephemeral Slack message to users who are denied access.

## Writing a Custom Provider

Create a module with a `Provider` class that extends `BaseAccessProvider`:

```python
# my_package/access/ldap.py
from slack_agents import UserContext
from slack_agents.access.base import (
    AccessDenied,
    AccessGranted,
    BaseAccessProvider,
)


class Provider(BaseAccessProvider):
    def __init__(self, *, server: str, group: str) -> None:
        self._server = server
        self._group = group

    async def check_access(self, *, context: UserContext) -> AccessGranted:
        # Look up context["user_id"] in your LDAP directory
        # and check group membership
        if is_member:
            return AccessGranted()
        raise AccessDenied(f"You need to be in the {self._group} group.")
```

`check_access` returns `AccessGranted` on success and raises `AccessDenied` on denial. The exception message is shown to the user as an ephemeral Slack message.

`UserContext` and `AccessGranted` are `TypedDict`s. `UserContext` contains:
- `user_id` — the user ID (required)
- `user_name` — display name (optional)
- `user_handle` — user handle (optional)
- `channel_id` — the channel ID (optional)
- `channel_name` — the channel name (optional)

Then reference it in config:

```yaml
access:
  type: my_package.access.ldap
  server: ldap://ldap.example.com
  group: agents-users
```

Any extra keys beyond `type` are passed as keyword arguments to the `Provider` constructor.
