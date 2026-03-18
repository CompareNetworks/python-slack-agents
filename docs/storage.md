# Adding a Storage Backend

Storage backends are Python modules that export a `Provider` class extending `BaseStorageProvider`.

## Two-level API

### 1. Required: 6 abstract primitives

Every backend **must** implement these. They are sufficient for a fully working system because all domain methods have default implementations built on them.

| Method | Description |
|--------|-------------|
| `get(namespace, key)` | Key-value read |
| `set(namespace, key, value)` | Key-value upsert |
| `delete(namespace, key)` | Key-value delete |
| `append(namespace, key, item)` | Append to an ordered list, returns item ID |
| `get_list(namespace, key)` | Read an ordered list |
| `query(namespace, filters)` | Equality-filter scan |

### 2. Optional: domain method overrides

Relational or indexed backends can override these for better performance:

- `get_or_create_conversation(...)` — conversation lifecycle
- `has_conversation(...)` — existence check
- `create_message(...)` — message creation
- `get_message_blocks(...)` — fetch blocks grouped by message
- `append_text_block(...)`, `append_file_block(...)`, `append_tool_block(...)`, `append_usage_block(...)` — block persistence
- `get_tool_call(tool_call_id)` — indexed tool-call lookup
- `upsert_heartbeat(...)`, `get_heartbeat(...)` — agent liveness
- `get_conversations_for_export(...)`, `get_messages_with_blocks(...)` — export queries
- `supports_export` property — whether export is available

The built-in PostgreSQL and SQLite providers override all of these with proper SQL.

## Minimal example: Redis

A Redis backend only needs the 6 primitives. All conversation management works automatically via the default implementations.

```python
# my_storage/redis.py
from slack_agents.storage.base import BaseStorageProvider

class Provider(BaseStorageProvider):
    def __init__(self, url: str):
        self._url = url

    async def initialize(self):
        # Connect to Redis
        ...

    async def get(self, namespace, key):
        ...

    async def set(self, namespace, key, value):
        ...

    async def delete(self, namespace, key):
        ...

    async def append(self, namespace, key, item):
        ...

    async def get_list(self, namespace, key):
        ...

    async def query(self, namespace, filters):
        ...

    async def close(self):
        ...
```

For better performance you could override specific domain methods — for example, `get_tool_call` with a Redis hash lookup by `tool_call_id` instead of scanning all blocks.

## Configuration

```yaml
storage:
  type: my_storage.redis
  url: "{REDIS_URL}"
```

## Key points

- Storage providers handle all persistence — the `ConversationManager` is a thin delegation layer
- `initialize()` is called at startup, `close()` at shutdown
- Non-relational backends only need the 6 abstract primitives
- Relational backends (PostgreSQL, SQLite) override domain methods with optimized SQL
