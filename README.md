# python-slack-agents

[![PyPI version](https://img.shields.io/pypi/v/python-slack-agents.svg)](https://pypi.org/project/python-slack-agents/)
[![Python](https://img.shields.io/pypi/pyversions/python-slack-agents.svg)](https://pypi.org/project/python-slack-agents/)
[![CI](https://github.com/CompareNetworks/python-slack-agents/actions/workflows/ci.yml/badge.svg)](https://github.com/CompareNetworks/python-slack-agents/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/CompareNetworks/python-slack-agents/blob/main/LICENSE)

A simple Python framework for running AI agents in Slack. Each agent is a YAML config and a system prompt — pick your LLM, connect some [MCP](https://modelcontextprotocol.io/) tools, and `slack-agents run`. Everything you need is included: streaming responses, file handling, conversation persistence, and a plugin system when you need to extend things. Built on [Bolt for Python](https://github.com/slackapi/bolt-python).

<p align="center">
  <img src="https://raw.githubusercontent.com/CompareNetworks/python-slack-agents/main/docs/media/demo.gif" alt="Agent streaming a response in Slack" width="800">
</p>

## What You Get

Each agent is a directory with two files: a `config.yaml` and a `system_prompt.txt`. Point it at your LLM, give it some tools, and run it.

**LLM providers** — Anthropic and OpenAI built in, plus any OpenAI-compatible API (Mistral, Groq, Together, Ollama, vLLM). Extend to any other provider by implementing a simple base class.

**Tool calling with MCP** — Connect any [MCP server](https://modelcontextprotocol.io/) over HTTP. Tools are discovered automatically, executed in parallel, and filtered with regex patterns. No tool registration boilerplate.

**File handling** — Agents can read files your users upload (PDF, DOCX, XLSX, PPTX, CSV, images) and generate documents back (PDF, DOCX, XLSX, CSV, PPTX). All built in, no extra setup.

**Streaming** — Responses stream token-by-token to Slack. Markdown tables are detected and rendered as native Slack tables, not code blocks.

**Conversation persistence** — SQLite for development, PostgreSQL for production. Conversations survive restarts, and you can export them to HTML or CSV.

**Access control** — Allow everyone, restrict to a list of Slack user IDs, or write a custom provider (LDAP, OAuth, whatever you need).

**Observability** — OpenTelemetry tracing out of the box. Send traces to Langfuse, Jaeger, Datadog, Grafana Tempo, or any OTLP-compatible backend.

**Docker** — Build per-agent Docker images with a single CLI command. Each agent is its own image with its own config.

**Plugin architecture** — LLM, storage, tools, and access control are all pluggable. Same pattern everywhere: a `type` field pointing to a Python module, and a simple `Provider` class in that module.

## Quick Start

```bash
mkdir my-agents && cd my-agents
python3 -m venv .venv
source .venv/bin/activate
pip install python-slack-agents

# Scaffold the project
slack-agents init my-agents

# Add your tokens and install framework + deps
cp .env.example .env                 # add your Slack and LLM tokens
pip install -r requirements.txt

# Run the hello-world agent
slack-agents run agents/hello-world
```

See [Setup](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/setup.md) for creating a Slack app and getting your tokens.

## How It Works

An agent is a directory with two files:

```
agents/my-agent/
├── config.yaml          # LLM, tools, storage, access control
└── system_prompt.txt    # what the agent should do
```

**config.yaml**

```yaml
version: "1.0.0"                     # shown in Slack usage footer, used as Docker image tag
schema: "slack-agents/v1"

slack:
  bot_token: "{SLACK_BOT_TOKEN}"
  app_token: "{SLACK_APP_TOKEN}"

llm:
  type: slack_agents.llm.anthropic
  model: claude-sonnet-4-6
  api_key: "{ANTHROPIC_API_KEY}"
  max_tokens: 4096
  max_input_tokens: 200000

storage:
  type: slack_agents.storage.sqlite
  path: ":memory:"

access:
  type: slack_agents.access.allow_all

tools:
  # Connect any MCP server — tools are auto-discovered
  web-search:
    type: slack_agents.tools.mcp_http
    url: "https://mcp.deepwiki.com/mcp"
    allowed_functions: [".*"]

  # Read uploaded files (PDF, DOCX, XLSX, PPTX, images, text)
  import-files:
    type: slack_agents.tools.file_importer
    allowed_functions: [".*"]

  # Generate and export documents (PDF, DOCX, XLSX, CSV, PPTX)
  export-documents:
    type: slack_agents.tools.file_exporter
    allowed_functions: ["export_pdf", "export_docx"]

  # Create, read, edit, and share Slack canvases
  canvas:
    type: slack_agents.tools.canvas
    bot_token: "{SLACK_BOT_TOKEN}"
    allowed_functions: [".*"]

  # Remember user preferences across conversations in a user-editable slack canvas
  user-context:
    type: slack_agents.tools.user_context
    bot_token: "{SLACK_BOT_TOKEN}"
    max_tokens: 1000
    allowed_functions: [".*"]
```

All secrets in `{ENV_VAR}` are resolved from environment variables at startup.

**system_prompt.txt** — plain text or markdown, as long or short as you need.

## CLI

```bash
slack-agents init <project-name>                    # scaffold a new project
slack-agents run agents/<name>                      # start an agent
slack-agents healthcheck agents/<name>              # liveness probe (for k8s)
slack-agents export-conversations agents/<name> \   # export conversation history
  --format html --output ./conversations
slack-agents export-usage agents/<name>  \          # export usage metrics to CSV
  --format csv --output ./usage.csv
slack-agents build-docker agents/<name>             # build a Docker image
slack-agents build-docker agents/<name> \           # custom image name
  --image-name my-bot
slack-agents build-docker agents/<name> \           # build and push to a registry
  --push registry.example.com
```

## Built-in Tools

### File Import

Users can upload files to the conversation. The agent automatically reads them:

| Format | What's extracted |
|--------|-----------------|
| PDF | Full text with layout preserved (via PyMuPDF) |
| DOCX | Text, headings, tables, lists |
| XLSX | Cell values across all sheets |
| PPTX | Slide text, speaker notes, tables |
| CSV, Markdown, plain text | Raw content |
| Images (PNG, JPEG, GIF, WebP) | Sent as vision input to the LLM |

### Document Export

The agent can generate and upload files to the conversation:

| Format | Capabilities |
|--------|-------------|
| PDF | Rich text, tables, headings, bullets, Unicode support |
| DOCX | Styled documents with tables and lists |
| XLSX | Multi-sheet spreadsheets |
| CSV | Tabular data export |
| PPTX | Slide decks with tables and speaker notes |

### Slack Canvases

[Canvases](https://slack.com/features/canvas) are collaborative documents embedded in Slack — think Google Docs, but native to your workspace. Multiple users and agents can read and write the same canvas, making them a shared surface for collaboration between people and AI. Agents can create, read, update, and manage canvases, which is useful for generating reports, maintaining living documents, or publishing structured content directly where your team works.

See [Canvas tool docs](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/canvas.md) for the full tool reference and setup.

### User Context (Optional Per-User Memory)

Each user can get a personal Slack canvas that stores their preferences and context across conversations. The agent loads it at the start of every conversation and offers to save important context when users share preferences or corrections. Users can also edit their canvas directly in Slack.

See [User context docs](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/user-context.md) for setup and configuration.

### MCP Servers

Connect any MCP server over HTTP. Tools are auto-discovered and can be filtered:

```yaml
tools:
  my-mcp-server:
    type: slack_agents.tools.mcp_http
    url: "https://my-server.example.com/mcp"
    headers:
      Authorization: "Bearer {MCP_API_TOKEN}"
    allowed_functions:
      - "search_.*"       # regex — only tools matching this pattern
      - "get_document"    # exact match works too
```

## Project Structure

Your overlay is a plain git repo — not a Python package. You edit configs, commit, and run; there's no
install-the-overlay-as-a-package step. A typical layout looks like:

```
my-agents/
├── requirements.txt        # pins python-slack-agents (and any extra deps)
├── .env.example
├── .gitignore
├── agents/
│   └── my-agent/
│       ├── config.yaml
│       └── system_prompt.txt
└── src/                    # optional — only if you add custom providers
    └── my_agents/
        └── __init__.py
```

Two conventions to know:

- **`src/` holds custom Python.** On `slack-agents run`, the framework walks up from the agent directory,
  finds the nearest `src/` sibling, and prepends it to `sys.path`. Anything under `src/<pkg>/...` is
  importable as `<pkg>.…` with no install step.
- **`requirements.txt` pins your framework version and any extra Python deps.** `pip install -r requirements.txt`
  is the only install command you ever run.

`slack-agents init` scaffolds this for you. See [Organizing agents](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/private-repo.md) for details.

## Extending

Every pluggable component follows the same pattern. Add a new LLM provider, storage backend, tool, or access policy by creating a module with a `Provider` class in `src/`:

```yaml
# In config.yaml
llm:
  type: my_agents.my_llm_provider
  model: my-model
  api_key: "{MY_API_KEY}"
```

```python
# In src/my_agents/my_llm_provider.py
from slack_agents.llm.base import BaseLLMProvider

class Provider(BaseLLMProvider):
    def __init__(self, model, api_key, **kwargs):
        ...
```

After you drop a module under `src/`, it's picked up automatically on the next `slack-agents run` —
the framework prepends `./src` to `sys.path` at startup. The same mechanism works inside Docker:
the bundled Dockerfile installs dependencies from `requirements.txt` and copies your `src/` into the
image, so custom providers resolve in production too.

See the docs for the full interface for each component:

- [LLM providers](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/llm.md)
- [Storage backends](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/storage.md)
- [Tool providers](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/tools.md)
- [Access control](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/access-control.md)

## Slack Compatibility

python-slack-agents uses **Socket Mode**, which connects to Slack over WebSocket. This means:

- **No public URL required** — works behind firewalls, on your laptop, in a private cluster (eg, k8s)
- **All Slack plans supported** — free, Pro, Business+, and Enterprise Grid
- **One process per agent** — Socket Mode requires a single WebSocket connection per app

Agents respond to @mentions in channels, direct messages, and thread replies. File uploads are automatically processed by file importer tools.

To create a Slack app, use the manifest in [`docs/slack-app-manifest.json`](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/slack-app-manifest.json) — it has all the required scopes and event subscriptions pre-configured.

## Requirements

- Python 3.12+
- A Slack app with Socket Mode enabled ([setup guide](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/setup.md))
- An API key for at least one LLM provider

## Documentation

- [Setup](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/setup.md) — installation and Slack app creation
- [Agents](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/agents.md) — creating and configuring agents
- [Tools](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/tools.md) — MCP servers and custom tool providers
- [LLM](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/llm.md) — supported providers and adding your own
- [Storage](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/storage.md) — SQLite, PostgreSQL, and custom backends
- [Access control](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/access-control.md) — controlling who can use an agent
- [Canvases](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/canvas.md) — creating and managing Slack canvases
- [User context](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/user-context.md) — per-user memory across conversations
- [Observability](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/observability.md) — OpenTelemetry tracing
- [Deployment](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/deployment.md) — Docker, docker-compose, and Kubernetes
- [CLI](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/cli.md) — command reference
- [Organizing agents](https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/private-repo.md) — in-repo, separate directory, or private repository

## For AI Agents

If you're an AI agent or coding assistant, see [`llms-full.txt`](https://raw.githubusercontent.com/CompareNetworks/python-slack-agents/main/llms-full.txt) for a complete, single-file reference to the config schema, all providers, and the plugin system. After `pip install`, the reference is available locally inside the package at `slack_agents/llms-full.txt`.

## Related Projects

Other projects in this space:

- **[Bolt for Python](https://github.com/slackapi/bolt-python)** — The official Slack SDK. python-slack-agents uses it internally. Use Bolt directly if you want full control over Slack interactions without an agent abstraction.
- **[bolt-python-ai-chatbot](https://github.com/slack-samples/bolt-python-ai-chatbot)** — Official Slack sample app for AI chatbots. A starting point if you want to build from scratch rather than use a framework.
- **[bolt-python-assistant-template](https://github.com/slack-samples/bolt-python-assistant-template)** — Official Slack template for building Agents & Assistants with Bolt and OpenAI.
- **[langgraph-messaging-integrations](https://github.com/langchain-ai/langgraph-messaging-integrations)** — Connects LangGraph agents to Slack and other messaging platforms.
- **[slack-mcp-client](https://github.com/tuannvm/slack-mcp-client)** — A Go application bridging Slack and MCP servers. Deployed app rather than a library.

## Disclaimer

This is an independent open-source project and is not affiliated with, endorsed by, or sponsored by Slack Technologies, LLC or Salesforce, Inc.

## License

Apache 2.0 — see [LICENSE](https://github.com/CompareNetworks/python-slack-agents/blob/main/LICENSE).
