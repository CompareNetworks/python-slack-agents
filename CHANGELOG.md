# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
