# Creating an Agent

Each agent lives in its own directory with two files:

## Directory Structure

```
agents/my-agent/
├── config.yaml
└── system_prompt.txt
```

The directory name (e.g. `my-agent`) is used by the CLI and as the default Docker image name. It has no effect on how the agent appears in Slack — the bot's display name is set when you create the Slack app and can be changed anytime in the [Slack app settings](https://api.slack.com/apps) under "App Home".

## config.yaml

```yaml
version: "1.0.0"
schema: "slack-agents/v1"

slack:
  bot_token: "{SLACK_BOT_TOKEN}"
  app_token: "{SLACK_APP_TOKEN}"

access:
  type: slack_agents.access.allow_all

llm:
  type: slack_agents.llm.anthropic
  model: claude-sonnet-4-6
  api_key: "{ANTHROPIC_API_KEY}"
  max_tokens: 4096
  max_input_tokens: 200000

storage:
  type: slack_agents.storage.sqlite
  path: ":memory:"

tools:
  import-documents:
    type: slack_agents.tools.file_importer
    allowed_functions: [".*"]
  my-mcp-server:
    type: slack_agents.tools.mcp_http
    url: "https://my-server.example.com/mcp"
    allowed_functions: [".*"]
```

### version (required)

A user-controlled string tracking changes to the agent's capabilities, system prompt, or configuration. We recommend semver (e.g. `"1.0.0"`, `"2.3.1"`) but any string is valid — the framework does not interpret it. The usage footer in Slack shows this version string instead of the model name. This version is also used as the Docker image tag when building with `slack-agents build-docker`.

### schema (required)

Identifies the config format version: `"slack-agents/v1"`. The framework uses this to determine if it can parse the config. If the config uses a schema newer than the installed version, startup fails with a clear error.

All `{ENV_VAR}` patterns are resolved from environment variables at startup.

## system_prompt.txt

Plain text file with the agent's system prompt:

```
You are a helpful assistant that specializes in...
```

## Running

```bash
slack-agents run agents/my-agent
```

## Slack App Setup

Each agent needs its own Slack app. Use the manifest in `docs/slack-app-manifest.json` as a starting point.

Key permissions needed:
- `app_mentions:read` — respond to @mentions
- `chat:write` — send messages
- `im:history`, `im:read`, `im:write` — handle DMs
- `files:read`, `files:write` — file attachments
- Socket Mode must be enabled
