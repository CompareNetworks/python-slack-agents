# AGENTS.md

Instructions for AI coding assistants working on this codebase.

## What This Is

A Python framework for deploying AI agents as Slack bots. One Docker image per agent — each agent has its own Slack app identity, system prompt, LLM provider, and tool servers. No LangChain/LlamaIndex; the agent loop is a custom ~200-line async generator.

## Commands

```bash
# Create and activate venv (one-time setup)
python3 -m venv .venv
source .venv/bin/activate

# Install for development
pip install -e ".[dev]"

# Run an agent locally
slack-agents run agents/<agent-dir>

# Check agent health (requires persistent storage)
slack-agents healthcheck agents/<agent-dir>

# Export conversations to HTML
slack-agents export-conversations agents/<agent-dir> --format=html

# Build Docker image for an agent
slack-agents build-docker agents/<agent-dir>

# Build and push to a registry
slack-agents build-docker agents/<agent-dir> --push registry.example.com

# Tests (asyncio_mode=auto, no flags needed)
pytest
pytest tests/test_format.py           # single file
pytest tests/test_format.py::test_name  # single test

# Lint and format
ruff check --fix src/ tests/
ruff format src/ tests/
```

All commands assume the `.venv` virtualenv is active.

Pre-commit hooks run ruff check+format automatically on commit.

## Architecture

**Plugin system:** All pluggable concerns (LLM, storage, tools) follow the same pattern: a `type` field with a dotted import path, and a `Provider` class in that module. `load_plugin(type_path, **kwargs)` loads any plugin.

**Startup:** `main.py` -> `load_agent_config()` returns `(config, system_prompt, agent_name)` -> `SlackAgent()` -> connects storage/tools/Slack Socket Mode.

**Per-message flow:** Slack event -> `agent.py._handle_message()` -> load conversation history via `ConversationManager` -> extract file attachments -> `run_agent_loop_streaming()` async generator -> `StreamingFormatter` routes text to `SlackStreamer` and tables to native `TableBlock` messages -> tool calls shown as Slack attachments -> usage footer posted -> response persisted.

**Key modules:**
- `agent_loop.py` -- Core LLM->tools->LLM loop (max 15 iterations, parallel tool execution via `asyncio.gather`), defines `ToolProvider` protocol
- `llm/base.py` -- `BaseLLMProvider` ABC, `StreamEvent` dataclass, internal Anthropic-style message format
- `llm/anthropic.py`, `llm/openai.py` -- Provider implementations (OpenAI provider converts at its boundary)
- `tools/base.py` -- `BaseToolProvider` and `BaseFileImporterProvider` ABCs with `allowed_functions` regex filtering
- `tools/mcp_http.py` -- MCP over HTTP/SSE tool provider
- `tools/file_exporter.py` -- Built-in document generation tool (PDF, DOCX, XLSX, CSV, PPTX)
- `tools/file_importer.py` -- Built-in file import provider (PDF, DOCX, XLSX, PPTX, text, images)
- `storage/base.py` -- `BaseStorageProvider` ABC (generic persistence layer)
- `storage/sqlite.py` -- SQLite storage provider (in-memory or file-based, via aiosqlite)
- `storage/postgres.py` -- PostgreSQL storage provider (asyncpg)
- `slack/agent.py` -- `SlackAgent` with Bolt AsyncApp, event routing, cost tracking
- `slack/conversations.py` -- `ConversationManager` wrapping storage with conversation logic
- `slack/streaming.py` + `streaming_formatter.py` -- Streaming output with table detection

**Internal message format is Anthropic-style throughout** (content as list of typed blocks: `text`, `tool_use`, `tool_result`). The OpenAI provider converts at its boundary via `_convert_messages()` and `_convert_tools()`.

## Agent Configuration

Each agent lives in `agents/<name>/` with `config.yaml` and `system_prompt.txt`. The agent name is derived from the directory name. Config supports `{ENV_VAR}` interpolation (uppercase + underscore patterns only).

**Top-level config fields:**
- `version` (required) -- user-controlled string shown in the usage footer **and used as the Docker image tag** when building with `slack-agents build-docker`. Track changes to the agent's prompts, tools, or behavior. The framework does not interpret this — it can be semver, a date, or any string.
- `schema` (required) -- config format identifier, currently `"slack-agents/v1"`. The framework checks this to ensure it can parse the config. Newer schemas fail with a clear upgrade message.

## Key Design Decisions

- **Async everywhere** -- all I/O (Slack, LLM, tools, storage) is async
- **Streaming as async generator** -- `run_agent_loop_streaming()` yields `StreamEvent` (text) and `dict` (tool status)
- **Unified tool interface** -- `BaseToolProvider` ABC (`.tools` + `.call_tool()`) for LLM-facing tools; `BaseFileImporterProvider` ABC (`.handlers`) for file import — both configured in `tools:` section, separated by isinstance in `_init_tools()`
- **Explicit configuration over silent defaults** -- do not auto-load providers when none are configured; if no `BaseFileImporterProvider` is in the config, file attachments are rejected with a clear error
- **Generic storage** -- `BaseStorageProvider` knows nothing about conversations; `ConversationManager` adds conversation logic on top
- **Lazy initialization** -- SlackStreamer creates stream on first delta; tools connect only when initialized
- **Caching-aware cost tracking** -- each LLM provider has `estimate_cost()` with provider-specific cache multipliers
- **Tool definitions in Anthropic format** -- `{"name", "description", "input_schema"}` is the canonical format everywhere
- **1 replica per agent** -- Socket Mode requires exactly one WebSocket connection per app

## AI Documentation Files

The project includes AI-agent-friendly documentation following the llms.txt convention:

- `llms.txt` (repo root) -- concise index pointing to docs and llms-full.txt
- `llms-full.txt` (repo root) -- generated from docs via `python3 src/slack_agents/scripts/generate_llms_full.py`
- `llms-full.txt` is bundled in the PyPI wheel via `force-include` in pyproject.toml

**When modifying docs:** re-run `python3 src/slack_agents/scripts/generate_llms_full.py` and commit the result.

## Releasing

1. Update `version` in `pyproject.toml`
2. Update `CHANGELOG.md` with the new version and changes
3. Run `python3 src/slack_agents/scripts/generate_llms_full.py` to regenerate `llms-full.txt`
4. Commit and push to `main`
5. Create a GitHub Release (which creates a git tag)
6. The `publish.yml` workflow automatically builds and publishes to PyPI via trusted publishing

The PyPI deployment requires manual approval in the GitHub Actions UI. Do NOT publish to PyPI manually — the GitHub Release trigger handles it.

## Style

- Python 3.12+, line length 100
- Ruff rules: E, F, I (errors, pyflakes, isort)
- Keep it simple. Minimal abstractions, no unnecessary indirection.
- Commit messages: Conventional Commits — `feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`. Lowercase, imperative, under 72 chars.
- **Always propose the commit message and wait for explicit user approval before committing or pushing.** Never commit or push autonomously.
