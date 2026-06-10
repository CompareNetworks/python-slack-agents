# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- `docs/oauth.md`: corrected the `OAUTH_SECRET_KEY` generation snippet. The documented `secrets.token_urlsafe(32)` emits URL-safe base64 (`-`/`_`, unpadded) that the validator (`base64.b64decode(..., validate=True)`) rejects; use `openssl rand -base64 32` or `base64.b64encode(secrets.token_bytes(32))` instead.

## [0.9.1] - 2026-06-09

### Added

- **A2A per-user OAuth** (`auth: { type: oauth2 }`). Remote A2A agents can now require per-Slack-user OAuth 2.1: each user authenticates separately and the agent calls on their behalf. Auth metadata is discovered from the Agent Card's `securitySchemes.oauth2` (no `client_id`/`scopes` in YAML); the flow uses Dynamic Client Registration + authorization-code + PKCE + refresh, the same ephemeral "Authenticate" button, the shared `/oauth/*` ingress, and the same `PUBLIC_URL` / `OAUTH_SECRET_KEY` env contract as `mcp_http_oauth`. Long-running tasks polled in the background use the user's stored token non-interactively (silent refresh; a "session expired — please re-ask" notice if it can't be refreshed). When push is registered for a task, the token-dependent poller is skipped (push delivers regardless of later token state). See `docs/a2a.md`.
- **A2A API-key auth** — `auth: { type: apiKey, name, value }` (sends the raw value as the named header, matching the Agent Card's `apiKey` scheme), alongside the existing `bearer` / `header` / `none` forms.
- **OAuth user-info logging** (MCP and A2A). On first authentication the user's OIDC identity (`sub` / `preferred_username` / `name` / `email`) is fetched from the userinfo endpoint and logged. The `profile` scope is now requested and registered alongside `openid` / `offline_access`.

### Changed

- The provider-agnostic OAuth flow was extracted from `tools/mcp_http_oauth.py` into the `oauth/` package (`scopes`, `errors`, `discovery`, `flow`), parameterized by a discovery strategy — RFC 9728 PRM for MCP, Agent Card for A2A. No behavior change for MCP-OAuth.
- DCR client registration now requests the OIDC baseline (`openid`, `offline_access`, `profile`) explicitly, so strict authorization-server registration policies (e.g. Keycloak's "Allowed Client Scopes") grant the client those scopes rather than rejecting the later authorize with `invalid_scope`.
- **BREAKING (A2A):** removed the `name` field from `slack_agents.a2a.agent`. The single tool is now named after the provider's key under `tools:` (slugified to satisfy LLM tool-name rules); rename the key to rename the tool. Keys are unique within `tools:`, so two agents never collide.
- **BREAKING (A2A):** removed `allowed_functions` from `slack_agents.a2a.agent` — an A2A agent exposes exactly one opaque tool, so there was nothing to filter. A stray `allowed_functions` on an A2A config is ignored at load with a warning.

### Fixed

- The in-process OAuth ingress crashed at startup with `TypeError: argument should be a bytes-like object … not 'bool'` whenever `OAUTH_SECRET_KEY` was set — `base64.b64decode(key, True)` passed `True` as the positional `altchars` argument instead of the keyword `validate=`. This was a latent bug in the shared ingress that affected MCP-OAuth and A2A-OAuth agents alike; it had no test coverage and is now exercised by `tests/test_ingress_startup.py`.
- A2A `call_tool` now maps OAuth-specific failures to actionable results — a user-level authorization denial becomes a permission-denied error naming the missing scope, and an IdP `redirect_uri` rejection clears the stale client registration so the next attempt self-heals — instead of a generic "contact support".
- The per-user A2A httpx client is closed if Agent Card resolution fails after construction, avoiding a connection-pool leak on the background poller's out-of-band re-auth path.

## [0.9.0] - 2026-06-07

### Added

- **A2A push notifications (receiving).** A push-capable agent (`capabilities.pushNotifications`) can deliver task updates — status messages and file artifacts — to the Slack thread out-of-band, including reports that arrive after a synchronous reply. Opt in per agent with `push_notifications: true`. The framework registers the webhook inline on the first send with a random per-task token, validates the `X-A2A-Notification-Token` header, correlates by `taskId`, and de-duplicates by message/artifact id (the server re-pushes the immediate reply). The OAuth HTTP sidecar is generalized into a shared ingress (one listener starting for OAuth or push), configured by the new `PUBLIC_URL` / `HTTP_BIND_HOST` / `HTTP_BIND_PORT` env vars (see Changed). Verified end-to-end against a live agent. See `docs/a2a.md`.
- **Agent2Agent (A2A) protocol integration** over the official `a2a-sdk` (1.x, protobuf), isolated behind `slack_agents.a2a.client`. Two topologies: `slack_agents.a2a.agent` exposes a remote A2A agent as a single free-text tool a real LLM delegates to (Option A, smart routing), and `slack_agents.a2a.proxy` turns Slack into a dumb frontend for one agent (Option B, dev/debugging). Features: per-thread `contextId` + `taskId` threading for correct multi-turn (`input-required`) conversations; synchronous replies plus detached background polling with out-of-band delivery for long-running (`working`) tasks (real LLMs re-process the result, the proxy posts it raw); bidirectional file attachments (Slack upload → A2A `raw` part, file artifact → Slack upload); static bearer/header auth. Includes an env-gated live integration test (`A2A_TEST_URL`). See `docs/a2a.md`.
- `docs/private-repo.md` — "Protecting secrets in your overlay" section covering GitHub push protection (server-side block that survives `--no-verify`), a gitleaks pre-commit hook, and a one-time trufflehog history sweep. Aimed at overlay maintainers whose configs reference Slack tokens, LLM API keys, and OAuth client secrets via `{ENV_VAR}` placeholders.
- `slack-agents init` now prints a visible "SECURITY: protect your secrets before pushing" banner at the end of scaffolding, linking to the new docs section.
- `SECURITY.md` — pointer for overlay maintainers to the overlay security guidance.

### Changed

- **BREAKING (OAuth):** the in-process HTTP listener is now a shared ingress for OAuth callbacks and A2A push. Its env vars were renamed — `OAUTH_PUBLIC_URL` → `PUBLIC_URL`, `OAUTH_BIND_HOST` → `HTTP_BIND_HOST`, `OAUTH_BIND_PORT` → `HTTP_BIND_PORT` — with **no aliases**. (`OAUTH_SECRET_KEY` is unchanged.) Agents using OAuth must rename these in their environment. The startup validator fails fast with a clear message naming `PUBLIC_URL` when OAuth or push is configured but the var is missing.

### Security

- Dependency security updates clearing all open advisories. Lifted the speculative `cryptography` upper cap (`<46`) that had blocked the patch (the fix shipped in the next major) and raised the floor to `>=46.0.7`; refreshed the lockfile to current releases: `cryptography` 48, `aiohttp` 3.14.1, `pillow` 12.2.0, `lxml` 6.1.1, `urllib3` 2.7.0, `python-multipart` 0.0.32, `starlette` 1.2.1, `anthropic` 0.107.1, `pygments` 2.20.0.

## [0.8.1] - 2026-05-07

### Fixed

- `oauth_clients` cache is now keyed by `(server_id, redirect_uri)` instead of `server_id` alone. Two `mcp_http_oauth` providers pointing at the same MCP server but with different `OAUTH_PUBLIC_URL` values (e.g. agents sharing a database, or a single agent whose tunnel hostname rotates) used to collide on the cached client registration; the second one would reuse a row whose `redirect_uri` the IdP no longer accepted, producing `Invalid parameter: redirect_uri` from the IdP.
- `mcp_http_oauth.Provider.call_tool` now detects an IdP `Invalid parameter: redirect_uri` (or `redirect_uri_mismatch`) rejection, deletes the stale cached client registration and the user's cached tokens, and surfaces a structured `system_error` with `code="redirect_uri_mismatch"` so the LLM can explain the situation. The next call re-registers a fresh client and prompts for re-auth.

### Migration

- The `oauth_clients` table primary key changed from `(server_id)` to `(server_id, redirect_uri)`. There is no automatic migration: any rows from 0.8.0 will be re-created on demand the first time each `(server_id, redirect_uri)` pair is used, leaving harmless orphan rows behind. Operators who want a clean slate can `DELETE FROM oauth_clients;` before upgrading.

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
