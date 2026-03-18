# Canvas Tool

The canvas tool lets your agent create, read, update, list, and delete [Slack canvases](https://slack.com/features/canvas) — rich documents that live inside Slack. It exposes a simple file-like API: no section IDs or low-level operations needed.

## Setup

### 1. Add Slack scopes

In your Slack app settings (**OAuth & Permissions → Scopes → Bot Token Scopes**), add:

| Scope | Purpose |
|-------|---------|
| `canvases:read` | Read canvas content and list canvases |
| `canvases:write` | Create, update, delete canvases and manage access |
| `files:read` | List canvases and read content (uses `files.list` / `files.info` APIs) |

After adding scopes, reinstall the app to your workspace.

### 2. Configure the tool

Add the canvas tool to your agent's `config.yaml`:

```yaml
tools:
  canvas:
    type: slack_agents.tools.canvas
    bot_token: "{SLACK_BOT_TOKEN}"
    allowed_functions: [".*"]   # all canvas tools
```

To expose only specific tools:

```yaml
    allowed_functions:
      - "canvas_create"
      - "canvas_get"
      - "canvas_update"
      - "canvas_list"
```

## Slack plan requirements

- **Free plans**: 1 canvas per channel/DM. `canvas_create` requires `channel_id`.
- **Paid plans** (Pro, Business+, Enterprise Grid): Unlimited canvases. `channel_id` is optional.

## Canvas content format

Canvas content is **markdown**. Supported elements:

- Headings (`#`, `##`, `###`)
- Bullet and numbered lists
- Tables
- Code blocks
- Block quotes
- Links
- Mentions (`<@U1234567890>`)
- Unfurls / embeds (`![](URL)`)

Block Kit is **not** supported in canvases.

## Available tools

| Tool | Description |
|------|-------------|
| `canvas_create` | Create a canvas with title + content. Optional `channel_id` to share it. |
| `canvas_get` | Get a canvas by ID. Returns title, full markdown content, and permalink. |
| `canvas_update` | Update a canvas — replace content, rename title, or both. |
| `canvas_delete` | Permanently delete a canvas. |
| `canvas_list` | List canvases visible to the bot. Optional `channel_id` filter. |
| `canvas_access_get` | Get sharing/access info for a canvas. |
| `canvas_access_add` | Grant read/write/owner access to users or channels. |
| `canvas_access_remove` | Remove access for users or channels. |

## Example usage

**Create a canvas in a channel:**
> "Create a canvas in #project-updates titled 'Q1 Roadmap' with our milestone list"

**Read and update a canvas:**
> "Get the canvas F12345 and update it with the latest status"

**Share a canvas with another team:**
> "Give the #design channel write access to canvas F12345"
