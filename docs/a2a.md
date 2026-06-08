# Agent2Agent (A2A) integration

`slack_agents.a2a.agent` and `slack_agents.a2a.proxy` connect this framework
to remote agents over the
[Agent2Agent protocol](https://a2a-protocol.org). A2A is an interaction
protocol — the remote agent is an opaque, possibly-conversational message
endpoint that describes itself via an Agent Card. The framework treats it as a
single free-text channel (not a menu of typed tools the way MCP does).

Built on the official [`a2a-sdk`](https://github.com/a2aproject/a2a-python)
(1.x, protobuf-based). All SDK-specific code is isolated in
`slack_agents.a2a.client`; the rest of the package depends only on a small
stable interface, so SDK version changes are contained to one module.

Two deployment topologies are supported from one codebase:

- **Option A — smart router.** A real local LLM (`slack_agents.llm.anthropic`,
  etc.) orchestrates a conversation and delegates to one or more remote A2A
  agents exposed as tools (`slack_agents.a2a.agent`). The local LLM decides
  when and how to invoke each agent, synthesizes the replies, and continues the
  conversation normally. Multiple A2A agents can coexist alongside other tool
  providers.
- **Option B — dumb frontend.** No local reasoning at all. `slack_agents.a2a.proxy`
  forwards every user message to a single remote A2A agent and relays the reply
  back. Slack becomes a thin chat front-end; the intelligence lives entirely in
  the remote agent. This is primarily a **development/debugging convenience** for
  poking at an A2A agent directly through Slack — Option A is the production shape.

## Configuration

### Option A — smart router

```yaml
llm:
  type: slack_agents.llm.anthropic
  model: claude-sonnet-4-6
  api_key: "{ANTHROPIC_API_KEY}"
  max_tokens: 4096
  max_input_tokens: 200000
tools:
  research-agent:
    type: slack_agents.a2a.agent
    url: "https://research.example.com"
    auth: { type: bearer, token: "{RESEARCH_A2A_TOKEN}" }
    poll_interval: 5
    max_task_lifetime: 3600
    allowed_functions: [".*"]
```

### Option B — dumb frontend

```yaml
llm:
  type: slack_agents.a2a.proxy
  model: "mya2a"
  max_input_tokens: 200000
tools:
  mya2a:
    type: slack_agents.a2a.agent
    url: "https://remote-agent.example.com"
    auth: { type: bearer, token: "{A2A_API_KEY}" }
    allowed_functions: [".*"]
```

The system prompt is ignored in Option B; it is used normally in Option A.

## `a2a.agent` — tool provider

`slack_agents.a2a.agent` is a `BaseToolProvider`. At startup it fetches the
remote agent's Agent Card, builds a single tool definition from the card's
name and description, and registers it like any other tool.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | `str` | required | Base URL of the remote agent. The framework tries `<url>/.well-known/agent-card.json` automatically. |
| `auth` | `dict` | none | Auth credentials. See [Auth](#auth) below. |
| `name` | `str` | — | Override the tool name derived from the Agent Card. Use this when the card's name would produce an inconvenient slug, or when two agents would otherwise collide. |
| `allowed_functions` | `list[str]` | required | Regex filters. Because every A2A provider exposes exactly one tool, `[".*"]` is almost always correct. |
| `timeout` | `float` | `300` | HTTP timeout in seconds for individual A2A requests. |
| `poll_interval` | `float` | `5` | Seconds between polling attempts for long-running tasks. |
| `max_task_lifetime` | `float` | `3600` | Maximum seconds to poll before abandoning a task and delivering a timeout message. |

**One tool per agent.** A2A is a single opaque channel — it has no mechanism to
advertise a menu of typed tools the way MCP does. Each `a2a.agent` provider
exposes exactly one free-text tool named after the agent (or overridden by
`name`):

```json
{
  "name": "<name>",
  "description": "<from Agent Card>",
  "input_schema": {
    "type": "object",
    "properties": { "message": { "type": "string" } },
    "required": ["message"]
  }
}
```

**Conversation continuity.** The framework tracks the A2A `contextId` (and, for
multi-turn tasks, the `taskId`) per Slack thread, transparently — the LLM never
sees or passes these. Successive messages in the same thread are sent on the
same `contextId` so the remote agent can keep conversational state.

When the agent replies with an **`input-required`** (or `auth-required`) state —
i.e. it is mid-task and wants another message — the framework relays the agent's
prompt to the user and **threads the same `taskId`** on the next message, so the
exchange continues the *same* Task (and its server-side state). When the task
reaches a terminal state, the saved `taskId` is cleared and the next message
starts a fresh Task. This is what makes multi-turn agents (e.g. a step-by-step
form or a guessing game) behave correctly instead of restarting each turn.

**Files.** File attachments flow in both directions. A file a user uploads in
Slack is forwarded to the agent as an A2A `raw` part (alongside the text); a
file the agent returns as an artifact is surfaced back into the Slack thread as
an upload. Non-text artifacts (CSV, PDF, …) come through as files; text
artifacts are used as the reply.

To **send** files you must also configure a file-import handler (e.g.
`slack_agents.tools.file_importer`) in `tools:` — the framework only accepts
uploads it has a handler for, and the a2a tool then forwards the raw bytes:

```yaml
tools:
  mya2a:
    type: slack_agents.a2a.agent
    url: "https://remote-agent.example.com"
    allowed_functions: [".*"]
  import-files:
    type: slack_agents.tools.file_importer
    allowed_functions: [".*"]
```

## `a2a.proxy` — passthrough LLM

`slack_agents.a2a.proxy` is a `BaseLLMProvider` that does no local reasoning.
It drives the same conversation loop as any other LLM provider, but instead of
calling an LLM it routes directly to the configured A2A tool.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | `str` | — | Name of the target `a2a.agent` tool (the key under `tools:` in `config.yaml`). When set, it is always used. When omitted, the proxy auto-selects the tool **only if exactly one** is configured; with more than one tool and no `model:`, startup fails with an error. |
| `max_input_tokens` | `int` | `200000` | Context-window guard, passed through to the loop. |

## Auth

Phase 1 supports static credentials only, resolved from environment variables
at startup via the standard `{ENV_VAR}` interpolation.

```yaml
# Bearer token — Authorization: Bearer <token>
auth: { type: bearer, token: "{A2A_API_KEY}" }

# Arbitrary header
auth: { type: header, name: "X-API-Key", value: "{A2A_API_KEY}" }

# No auth (default when omitted)
auth: { type: none }
```

Per-user OAuth auth (reusing the `oauth/` package) is planned for a future
release.

## Long-running tasks

The A2A protocol distinguishes between tasks that complete quickly and tasks
that may take minutes or hours. The framework handles both transparently.

The client sends every message with `blocking: true`, requesting that the
server wait for completion before responding. This is only a hint — the server
is free to return a non-terminal (`working`) task for long jobs.

**Synchronous path.** If the remote agent returns a terminal result
(`completed`, `failed`, `canceled`, or `rejected`) immediately, the result is
returned inline to the LLM (Option A) or relayed directly to Slack (Option B).

**Background-polling path.** If the remote agent returns a non-terminal
(`submitted` or `working`) task, the framework:

1. Persists an in-flight record (task ID, context, thread, channel) to the
   storage backend.
2. Returns an acknowledgement to the LLM/proxy immediately
   ("Started a longer task — I'll post the result here when it's ready.").
3. Spawns a background poller that calls `tasks/get` every `poll_interval`
   seconds until the task reaches a terminal state or `max_task_lifetime` is
   exceeded.
4. On completion, delivers the result **out-of-band** into the original Slack
   thread:
   - **Option A (real LLM):** the result is injected as a synthetic inbound
     turn and the standard loop re-runs, so the local LLM can interpret or act
     on it before replying to the user.
   - **Option B (proxy):** the result text is posted directly to the thread
     without re-entering the loop (the proxy has no intelligence to add).

This is entirely outbound — the background poller calls the remote agent's
`tasks/get` endpoint on a timer. No public URL or inbound HTTP listener is
needed. The Socket Mode deploy-anywhere property is preserved.

**Crash resilience.** In-flight task records are persisted in the storage
backend. If the agent process restarts while tasks are in flight, they are
re-discovered at startup and pollers are resumed automatically.

## What a Slack user sees

**For a fast response (synchronous path):**

1. User asks the bot something.
2. The bot forwards the message to the remote A2A agent and replies with the
   result, same as any other tool call.

**For a long-running task (async path):**

1. User asks the bot something.
2. The bot replies: "Started a longer task — I'll post the result here when
   it's ready."
3. When the remote agent finishes, the result appears in the same Slack thread.

## Trying it against a reference agent

You don't need to write an A2A agent to try the integration — point it at the
official [`helloworld`](https://github.com/a2aproject/a2a-samples/tree/main/samples/python/agents/helloworld)
sample (a2a-sdk 1.x, no API key). It's a simple echo, so it exercises the core
path — Agent Card resolution, send, completion, artifact handling — but not
multi-turn or files.

```bash
git clone --depth 1 https://github.com/a2aproject/a2a-samples /tmp/a2a-samples
cd /tmp/a2a-samples/samples/python/agents/helloworld

# The upstream Containerfile's CMD passes --host 0.0.0.0, but __main__.py hardcodes
# 127.0.0.1, so it binds container-loopback and isn't reachable from the host.
# Make it bind 0.0.0.0 (the card's advertised url stays 127.0.0.1:9999, which is
# correct for a client on the host):
sed -i '' "s/host='127.0.0.1'/host='0.0.0.0'/" __main__.py   # GNU sed: use  sed -i

docker build -f Containerfile -t helloworld-a2a-server .
docker run -d -p 9999:9999 helloworld-a2a-server
```

Set `url: "http://127.0.0.1:9999"` in your `a2a.agent` config and message the
bot — it replies `Hello, World! I have received your request (...)`. A ready-made
example lives at [`agents/a2a-agent/config.yaml`](../agents/a2a-agent/config.yaml),
which documents both the smart-routing and proxy topologies (proxy commented out
for easy switching) and repeats these server-start steps inline.

**Integration test.** `tests/a2a/test_integration.py` drives the real client
against a live agent and is **skipped unless `A2A_TEST_URL` is set**, so it
never affects CI:

```bash
A2A_TEST_URL=http://127.0.0.1:9999 pytest tests/a2a/test_integration.py -v
```

The assertions are agent-agnostic — the same test works against any conformant
A2A agent.

## Push notifications

When a remote agent supports push (`capabilities.pushNotifications`), the
framework can register a **webhook** so the agent delivers task updates —
status messages and file artifacts — to the Slack thread out-of-band, including
reports that arrive *after* a synchronous reply. This is the unified,
**push-preferred** async path; the background poller remains the fallback for
agents that don't support push.

**Enable it** with `push_notifications: true` on the `a2a.agent` config (opt-in,
because push requires a publicly reachable URL):

```yaml
tools:
  mya2a:
    type: slack_agents.a2a.agent
    url: "https://remote-agent.example.com"
    push_notifications: true
    allowed_functions: [".*"]
```

**Ingress.** Push needs the in-process HTTP listener (shared with OAuth) and a
public URL the agent can POST to. Configure:

| Env | Default | Purpose |
|-----|---------|---------|
| `PUBLIC_URL` | — (required) | Public base URL of the ingress; the webhook is `<PUBLIC_URL>/a2a/push`. |
| `HTTP_BIND_HOST` | `0.0.0.0` | Listener bind host. |
| `HTTP_BIND_PORT` | `8080` | Listener bind port. |

This is the one A2A feature that needs **inbound** HTTP — unlike the polling
path, which is outbound-only. For local testing the URL can be a loopback
(`http://127.0.0.1:8080`) reachable by an agent on the same host.

**How it works.** On the first send the framework registers the webhook inline
(`SendMessageConfiguration.task_push_notification_config`) with a random
per-task token, and persists a `taskId → thread` record. Incoming POSTs are
validated against the token, correlated by `taskId`, **de-duplicated** by
message/artifact id (the server re-pushes the immediate reply, which we already
delivered synchronously), and any genuinely-new text/files are posted/uploaded
to the thread.

**Security & limits.** We validate the shared-secret token and only act on tasks
we registered. The protocol's signing (JWS) and SSRF-allowlist are server-side
concerns we don't yet rely on. There are no delivery retries (the agent sends a
single POST), and a *server* restart drops its own registration — so a
previously-registered task simply stops pushing.

## Current limitations

The following are not yet implemented. They are planned for future releases.

- **Files: image uploads and the async path.** *Receiving* files works for any
  type. When *sending*, only non-image uploads (CSV, PDF, DOCX, …) are forwarded
  — images are not yet — and files are forwarded/surfaced only on the synchronous
  path (a long-running task delivered out-of-band carries its text, not files).
- **Atomic responses.** Responses are collected in full before being posted.
  Token-by-token streaming to Slack is not supported yet.
- **Static auth only.** Auth credentials are fixed at startup from environment
  variables. Per-Slack-user OAuth (each user authenticates separately to the
  remote agent) is not yet supported.
- **No A2A server mode.** This framework cannot be addressed as an A2A agent
  by other agents. It is a client only.

## Troubleshooting

**"A2A agent returned a long-running task but async delivery is unavailable."**
— the `a2a.agent` provider was not given a `framework_ctx` (injected
automatically by the framework when the provider is loaded via `config.yaml`).
This should not happen in normal operation; it can appear in test setups that
construct the provider directly without the framework context.

**The background poller never delivers a result.** Check that `poll_interval`
and `max_task_lifetime` are appropriate for the remote agent. If the agent
takes longer than `max_task_lifetime`, the task is abandoned and a timeout
message is posted to the thread. Increase `max_task_lifetime` if needed.

**"a2a.proxy: set `model:` to the target a2a tool name"** — you have more than
one tool configured and the proxy doesn't know which one to route to. Set
`model:` under the `llm:` section to the key of the intended `a2a.agent` tool.
