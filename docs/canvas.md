# Canvas Tool

The canvas tool lets your agent create, read, update, and delete [Slack canvases](https://slack.com/features/canvas) — rich documents that live inside Slack. It exposes a simple file-like API: no section IDs or low-level operations needed.

## Setup

### 1. Add Slack scopes

In your Slack app settings (**OAuth & Permissions → Scopes → Bot Token Scopes**), add:

| Scope | Purpose |
|-------|---------|
| `canvases:read` | Read canvas content |
| `canvases:write` | Create, update, delete canvases and manage access |
| `files:read` | Read canvas content and check user access (uses `files.info` API) |

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
```

### 3. Canvas file importer (optional)

To let users attach canvases to messages and have the agent read them automatically, add the canvas importer:

```yaml
tools:
  canvas-importer:
    type: slack_agents.tools.canvas_importer
    bot_token: "{SLACK_BOT_TOKEN}"
    allowed_functions: [".*"]
```

When a user attaches a canvas (mimetype `application/vnd.slack-docs`) to a message, the importer reads its markdown content via the Slack API and includes it in the conversation context. Authorization is enforced — the agent only reads canvases the requesting user can access.

## Authorization model

All canvas operations enforce **user-level permissions**. The agent acts as a delegate for the requesting user — it will not access canvases the user can't access themselves.

Access is resolved from `files.info` metadata (no extra storage or scopes needed):

| Check | Source field |
|-------|-------------|
| Is user the creator? | `user` / `canvas_creator_id` |
| Per-user access | `dm_mpdm_users_with_file_access` |
| Workspace-wide access | `org_or_workspace_access` |

**Access levels** (higher includes lower): `owner` > `write` > `read`

**Required access per tool:**

| Tool | Required |
|------|----------|
| `canvas_create` | — (no existing canvas) |
| `canvas_get` | read |
| `canvas_update` | write |
| `canvas_delete` | owner |
| `canvas_access_get` | read |
| `canvas_access_add` | owner |
| `canvas_access_remove` | owner |

If the user lacks sufficient access, the tool returns an error message explaining what access level is needed.

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
| `canvas_create` | Create a standalone canvas with title + content. |
| `canvas_get` | Get a canvas by ID. Returns title, full markdown content, and permalink. |
| `canvas_update` | Update a canvas — replace content, rename title, or both. |
| `canvas_delete` | Permanently delete a canvas. |
| `canvas_access_get` | Get sharing/access info for a canvas. |
| `canvas_access_add` | Grant read/write/owner access to users. Optionally set `org_access` for workspace-wide access. |
| `canvas_access_remove` | Remove access for users. |

## Example usage

**Create a canvas:**
> "Create a canvas titled 'Q1 Roadmap' with our milestone list"

**Read and update a canvas:**
> "Get the canvas F12345 and update it with the latest status"

**Share a canvas with specific users:**
> "Give users U123 and U456 write access to canvas F12345"
