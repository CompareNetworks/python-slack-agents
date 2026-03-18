# User Context (Per-User Memory)

The user context tool gives each user a personal Slack canvas that stores their preferences and context across conversations. The agent checks it at the start of every conversation to personalize responses, and offers to save important context when users share preferences or corrections.

Users can also edit their canvas directly in Slack to add or update preferences.

## Setup

### 1. Add Slack scopes

In your Slack app settings (**OAuth & Permissions → Scopes → Bot Token Scopes**), add:

| Scope | Purpose |
|-------|---------|
| `canvases:read` | Read the user's context canvas |
| `canvases:write` | Create and update user context canvases |
| `files:read` | Read canvas content (uses `files.info` API) |

These are the same scopes required by the [canvas tool](canvas.md). After adding scopes, reinstall the app to your workspace.

### 2. Configure the tool

Add the user-context tool to your agent's `config.yaml`:

```yaml
tools:
  user-context:
    type: slack_agents.tools.user_context
    bot_token: "{SLACK_BOT_TOKEN}"
    max_tokens: 1000           # limit on context size
    allowed_functions: [".*"]
```

| Option | Default | Description |
|--------|---------|-------------|
| `bot_token` | *(required)* | Slack bot token with canvas scopes |
| `max_tokens` | `1000` | Maximum token budget for user context |
| `allowed_functions` | *(required)* | Regex patterns for which tools to expose |

## How it works

1. **At conversation start**, the agent calls `get_user_context` to load the user's saved preferences.
2. **During conversation**, if the user shares preferences or corrections worth remembering, the agent offers to save them via `set_user_context`.
3. **Canvas creation is lazy** — no canvas is created until the first `set_user_context` call. The canvas is titled `"{agent_name} ({user_name})"` and the user is granted write access.
4. **Users can edit directly** — the canvas is a regular Slack canvas that users can open and edit in Slack at any time.

## Available tools

| Tool | Params | Description |
|------|--------|-------------|
| `get_user_context` | *(none — uses conversation context)* | Load the user's saved context. Returns `{content, permalink}` or empty content. |
| `set_user_context` | `agent_name`, `content` | Save/replace the user's context. Creates the canvas on first use. |

## Storage

Canvas IDs are stored using the agent's storage backend with namespace `user_context_canvas`. The storage key includes the bot user ID to avoid collisions when multiple agents share a database.

## Example interaction

> **User:** I prefer concise bullet-point answers, not long paragraphs.
>
> **Agent:** Got it! Would you like me to save that preference so I remember it in future conversations?
>
> **User:** Yes please.
>
> **Agent:** Saved your preference. You can also edit it directly anytime:
> https://slack.com/docs/T.../F...
