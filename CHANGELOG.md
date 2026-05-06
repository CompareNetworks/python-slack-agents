# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.8.0] - 2026-05-06

### Added

- `slack_agents.tools.mcp_http_oauth` — OAuth-authenticated MCP tool provider with per-Slack-user tokens. Each user authenticates separately to the upstream service; refresh tokens are AES-GCM-encrypted at rest. The provider runs an in-process aiohttp callback listener alongside Slack Bolt's WebSocket connection — no public ingress beyond a single `/oauth/callback` path. Includes Dynamic Client Registration with PRM-driven scope catalog, scope-merging on every authorize request (OIDC baseline + cached-token scopes + server-hinted scopes), and post-step-up permission-denied detection. See `docs/oauth.md`.
- `slack_agents.oauth/` package — signed-state codec, HKDF/AES-GCM crypto, callback listener, ephemeral auth-prompt builder, `DBTokenStorage` bridging the MCP SDK's `TokenStorage` Protocol to the agent's storage backend.
- `oauth_tokens` and `oauth_clients` tables on both SQLite and PostgreSQL backends (created idempotently at startup).
- `FrameworkContext` injection in `load_plugin` — providers that declare a `framework_ctx` parameter receive a shared object holding the bot token, Slack client, storage backend, and OAuth pending-flows registry. Existing providers are unaffected.
- `validate_oauth_env(tools_config)` consolidated startup check. Required env vars when at least one `mcp_http_oauth` provider is configured: `OAUTH_PUBLIC_URL`, `OAUTH_SECRET_KEY`. Optional: `OAUTH_BIND_HOST` (default `0.0.0.0`), `OAUTH_BIND_PORT` (default `8080`). Missing/malformed values produce a single, actionable error and refuse to start.
- Eager-auth pre-LLM hook in `SlackAgent` — each user's first message triggers OAuth setup before the LLM is invoked, so the LLM sees real tool lists rather than an empty/placeholder set.
- `make_tool_error(...)` helper plus `ERROR_*` and `RECOVERY_*` constants in `slack_agents.tools.base`. Uniform JSON schema for tool-error payloads in `ToolResult.content` so the LLM consuming a tool result can reason about errors (permission_denied / system_error / input_error / auth_setup_failed) uniformly. The `recovery` enum (retry / contact_admin / contact_support / abort) drives how the LLM should advise the user. See `docs/tools.md` "Tool error schema".
- LLM error classification in `slack/agent.py` — transient provider errors (`overloaded_error`, `rate_limit_error`, `api_error`, `timeout_error`) produce friendly user messages and log at WARNING (no traceback noise); configuration errors (`authentication_error`, `permission_error`, `not_found_error`, `invalid_request_error`) stay ERROR with full traceback.
- `cryptography` runtime dependency (HKDF + AES-GCM).
- `docs/oauth.md` — operator guide, scope-handling story, MCP SDK workarounds, troubleshooting (Trusted Hosts and Allowed Client Scopes policy gates, post-step-up permission denial).

### Changed

- Every built-in tool error site (`mcp_http`, `mcp_http_oauth`, `canvas`, `user_context`, `file_exporter`, plus `agent_loop`'s unknown-tool fallback) now emits the unified structured-error JSON schema in `ToolResult.content` when `is_error=True`. Custom tools should use `make_tool_error(...)`.
- The Slack-user-facing message on unrecognized exceptions ("Sorry, I encountered an error processing your request.") is now the fallback only — known LLM-provider errors get specific messages instead.
- `docs/tools.md` — example uses `make_tool_error` for the unknown-tool branch; new "Tool error schema" section.

## [0.7.0] - 2026-04-14

### Changed

- **Overlays are no longer Python packages.** `slack-agents init` scaffolds a plain git repo with `requirements.txt` (pinning the currently-installed framework version) instead of `pyproject.toml`. No more `pip install -e .` step for overlays — users run `pip install -r requirements.txt` and are done.
- The framework CLI now walks up from the agent directory on startup, finds the nearest `src/` sibling, and prepends it to `sys.path`. Custom providers under `src/<pkg>/` resolve without installing the overlay as a pip package.
- Bundled Dockerfile installs overlay dependencies from `requirements.txt` (or PEP 735 `[dependency-groups]` as an alternative) instead of running `pip install .`. The `README.md` / `llms-full.txt` placeholder workaround is gone.
- Scaffolder `.gitignore` drops `*.egg-info/` and `dist/` (overlays no longer build wheels).

### Added

- `_auto_extend_sys_path()` helper in `slack_agents.config`, called from `load_agent_config()` before any plugin import.
- End-to-end overlay integration test covering scaffold → auto-sys.path → custom provider resolution, plus Dockerfile-shape assertions.

### Removed

- `build-docker` no longer rejects overlays with `req*.txt` files — that file is now the expected input.
- `slack-agents init` no longer emits `pyproject.toml` or warns about requirements files.
- "Framework Development" section removed from `docs/setup.md` — contributors see `CONTRIBUTING.md` instead, keeping user-facing docs focused on overlay users.

### Docs

- Full rewrite of `docs/private-repo.md` around a single-path overlay model. PEP 735 `[dependency-groups]` documented as an alternative for teams who want `pyproject.toml` without `[project]`.
- `README.md` "Project Structure" and "Extending" sections rewritten to match.

### Migration

- Delete your overlay's `pyproject.toml` and any `*.egg-info/` directories.
- Add a `requirements.txt` pinning `python-slack-agents==0.7.0` (or `<2`).
- Run `pip install -r requirements.txt`.
- Custom providers under `src/<pkg>/` work without `pip install -e .`.

## [0.6.3] - 2026-03-31

### Fixed

- Preserve agent name in Docker image for multi-agent database support (`COPY` uses `${AGENT_NAME}` so each image's agent directory keeps its identity).
- Remove `libmupdf-dev` from the Docker image — image size down to 354 MB.

## [0.6.2] - 2026-03-19

### Added

- Canvas user-level authorization — tools enforce requesting user's access level via `files.info` metadata
- Canvas file importer (`application/vnd.slack-docs`) — users can attach canvases to messages
- `file_id` field on `InputFile` — file import pipeline now passes Slack file IDs to handlers
- `org_access` parameter on `canvas_access_add` for workspace-wide access

### Changed

- Canvas tool descriptions instruct the LLM to guide users to attach canvases via Slack's + button (never ask for IDs)
- Canvas tool errors now return structured JSON instead of plain text

### Removed

- `canvas_list` tool (scaling concern with batch `files.info`; users discover canvases via Slack UI)
- `channel_id` parameter from `canvas_create` (standalone canvases only)
- `channel_ids` parameter from `canvas_access_add` and `canvas_access_remove`

## [0.6.1] - 2026-03-19

### Added

- `slack-agents init` now generates `.gitignore` 
- `.env.example` template includes comments explaining where to get each token and links to setup guide
- `build-docker` lists required environment variables after build completes
- `build-docker` errors if `req*.txt` files are found (dependencies must be in `pyproject.toml`)
- `init` warns when `req*.txt` files are found with migration instructions

### Changed

- `pyproject.toml` template uses `python-slack-agents<2` (no minimum pin) 
- Setup flow uses venv-first approach: create venv, install package, then `slack-agents init`
- Updated README, docs/setup.md, and docs/private-repo.md with new setup flow

### Fixed

- Config loader now strips YAML comments before env var interpolation — commented-out `{ENV_VAR}` patterns no longer cause `KeyError`
- `init` shows proposed file content when skipping existing files

## [0.6.0] - 2026-03-18

### Added

- `slack-agents init <project_name>` CLI command to scaffold new projects
- `llms.txt` and `llms-full.txt` for AI agent discoverability
- `llms-full.txt` bundled in PyPI wheel
- Script to generate `llms-full.txt` from docs (`src/slack_agents/scripts/generate_llms_full.py`)
- "Project Structure" section in README
- Release process documentation in AGENTS.md

### Changed

- Simplified Dockerfile: empty placeholders for README.md and llms-full.txt so builds work for both framework and user projects
- Updated docs/private-repo.md to use `slack-agents init`
- Updated docs/cli.md with `init` command reference

## [0.5.0] - 2025-03-13

### Added

- Plugin architecture for LLM providers, storage backends, and tools
- Anthropic and OpenAI LLM providers
- SQLite and PostgreSQL storage providers
- MCP over HTTP tool provider
- Built-in document export tools (PDF, DOCX, XLSX, CSV, PPTX)
- Streaming output with native Slack table rendering
- Socket Mode support (no public URL required)
- OpenTelemetry observability
- `{ENV_VAR}` interpolation in agent configs
- Per-agent Docker builds via `docker-build-and-push.sh`
